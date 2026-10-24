//! Studio + auto-share: a cleanly stopped Studio recording is rendered with
//! the saved export preferences and uploaded to a share link with nobody in
//! the editor, the Loom/Tella flow on top of a recording that stays editable.
//!
//! The link is reserved before the render starts, so it reaches the clipboard
//! and the browser while the share page is still showing its upload state.
//! The upload then lands in `meta.sharing` exactly as the editor's own share
//! does, which is what lets a later edit's "Reupload to same link" replace it.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use cap_project::{RecordingMeta, RecordingMetaInner, StudioRecordingStatus};
use gpui::{App, AppContext as _, AsyncApp, Context, Entity, Global, Task, WeakEntity};

use crate::upload::UploadResult;

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum ShareStage {
    Reserving,
    Rendering(f32),
    Uploading(f32),
    Done,
    Failed(String),
}

pub(crate) struct ShareJob {
    pub project_path: PathBuf,
    pub name: String,
    pub link: Option<String>,
    pub stage: ShareStage,
    link_announced: bool,
    cancel: Arc<AtomicBool>,
    _task: Option<Task<()>>,
}

impl ShareJob {
    pub fn is_running(&self) -> bool {
        !matches!(self.stage, ShareStage::Done | ShareStage::Failed(_))
    }

    /// One line for the main window and the tray.
    pub fn status_label(&self) -> String {
        match &self.stage {
            ShareStage::Reserving => "Creating share link…".into(),
            ShareStage::Rendering(progress) => {
                format!("Rendering… {}%", (progress * 100.).round() as u32)
            }
            ShareStage::Uploading(progress) => {
                format!("Uploading… {}%", (progress * 100.).round() as u32)
            }
            ShareStage::Done => "Shared, link copied".into(),
            ShareStage::Failed(error) => format!("Share failed: {error}"),
        }
    }

    pub fn progress(&self) -> Option<f32> {
        match self.stage {
            ShareStage::Rendering(progress) => Some(progress * 0.5),
            ShareStage::Uploading(progress) => Some(0.5 + progress * 0.5),
            ShareStage::Done => Some(1.),
            ShareStage::Reserving | ShareStage::Failed(_) => None,
        }
    }
}

#[derive(Default)]
pub(crate) struct AutoShare {
    jobs: Vec<ShareJob>,
}

struct AutoShareGlobal(Entity<AutoShare>);
impl Global for AutoShareGlobal {}

impl AutoShare {
    pub fn init(cx: &mut App) {
        let entity = cx.new(|_| Self::default());
        cx.set_global(AutoShareGlobal(entity));
    }

    pub fn global(cx: &App) -> Entity<Self> {
        cx.global::<AutoShareGlobal>().0.clone()
    }

    /// Whether the next clean Studio stop is shared instead of opened in the
    /// editor: the setting is on and there is an account to share to. Signed
    /// out, Studio keeps its normal post-recording behaviour.
    pub fn applies() -> bool {
        crate::store::auto_share_studio() && crate::store::auth_snapshot().signed_in()
    }

    /// Whether a share of this project is rendering or uploading, so the
    /// editor does not race it with its own upload of the same file.
    pub fn is_busy(cx: &App, project_path: &Path) -> bool {
        cx.try_global::<AutoShareGlobal>().is_some_and(|global| {
            global
                .0
                .read(cx)
                .jobs
                .iter()
                .any(|job| job.project_path == project_path && job.is_running())
        })
    }

    /// The job the main window shows: the newest one.
    pub fn latest(&self) -> Option<&ShareJob> {
        self.jobs.last()
    }

    pub fn start(&mut self, project_path: PathBuf, cx: &mut Context<Self>) {
        if self
            .jobs
            .iter()
            .any(|job| job.project_path == project_path && job.is_running())
        {
            return;
        }
        let name = match shareable_name(&project_path) {
            Ok(name) => name,
            Err(error) => {
                tracing::warn!(path = %project_path.display(), error, "auto-share skipped");
                return;
            }
        };
        self.jobs.retain(|job| job.project_path != project_path);
        let cancel = Arc::new(AtomicBool::new(false));
        let task = cx.spawn({
            let project_path = project_path.clone();
            let cancel = cancel.clone();
            async move |this, cx| run(this, project_path, cancel, cx).await
        });
        self.jobs.push(ShareJob {
            project_path,
            name,
            link: None,
            stage: ShareStage::Reserving,
            link_announced: false,
            cancel,
            _task: Some(task),
        });
        cx.notify();
    }

    pub fn retry(&mut self, project_path: PathBuf, cx: &mut Context<Self>) {
        self.start(project_path, cx);
    }

    /// Drops a finished job's toast; a running one is cancelled first.
    pub fn dismiss(&mut self, project_path: &Path, cx: &mut Context<Self>) {
        if let Some(job) = self
            .jobs
            .iter()
            .find(|job| job.project_path == project_path)
        {
            job.cancel.store(true, Ordering::Release);
        }
        self.jobs.retain(|job| job.project_path != project_path);
        cx.notify();
    }

    fn job_mut(&mut self, project_path: &Path, cancel: &Arc<AtomicBool>) -> Option<&mut ShareJob> {
        self.jobs
            .iter_mut()
            .find(|job| job.project_path == project_path && Arc::ptr_eq(&job.cancel, cancel))
    }
}

fn shareable_name(project_path: &Path) -> Result<String, String> {
    let meta = RecordingMeta::load_for_project(project_path).map_err(|error| error.to_string())?;
    match &meta.inner {
        RecordingMetaInner::Studio(studio)
            if matches!(studio.status(), StudioRecordingStatus::Complete) =>
        {
            Ok(meta.pretty_name.clone())
        }
        RecordingMetaInner::Studio(_) => Err("the recording is not complete".into()),
        _ => Err("only Studio recordings are auto-shared".into()),
    }
}

/// The export panel's organisation: the saved choice while the account still
/// has it, otherwise the account's first.
fn organization_id() -> Option<String> {
    let organizations = crate::store::auth_snapshot().organizations;
    crate::store::load()
        .export
        .and_then(|prefs| prefs.organization_id)
        .filter(|id| organizations.iter().any(|org| &org.id == id))
        .or_else(|| organizations.first().map(|org| org.id.clone()))
}

fn upload_failure(result: UploadResult) -> Option<String> {
    match result {
        UploadResult::Success(_) => None,
        UploadResult::NotAuthenticated => Some("Sign in to share recordings".into()),
        UploadResult::UpgradeRequired => {
            Some("This recording is longer than your plan allows to share".into())
        }
    }
}

async fn run(
    this: WeakEntity<AutoShare>,
    project_path: PathBuf,
    cancel: Arc<AtomicBool>,
    cx: &mut AsyncApp,
) {
    let organization = organization_id();
    let update = |cx: &mut AsyncApp, change: &dyn Fn(&mut ShareJob, &mut App)| {
        this.update(cx, |this, cx| {
            if let Some(job) = this.job_mut(&project_path, &cancel) {
                change(job, cx);
                cx.notify();
            }
        })
        .is_ok()
    };

    let reserved = gpui_tokio::Tokio::spawn(
        cx,
        crate::upload::reserve_share_link(project_path.clone(), organization.clone()),
    )
    .await
    .map_err(|error| error.to_string())
    .and_then(|result| result);
    match reserved {
        Ok(UploadResult::Success(link)) => {
            update(cx, &|job, cx| announce(job, &link, cx));
        }
        Ok(other) => {
            let error = upload_failure(other).unwrap_or_default();
            update(cx, &|job, _| job.stage = ShareStage::Failed(error.clone()));
            return;
        }
        // The early link is a convenience: the upload reserves its own id if
        // this one never got one, and the link goes out when it lands instead.
        Err(error) => tracing::warn!(error, "auto-share could not reserve its link early"),
    }
    if cancel.load(Ordering::Acquire) {
        return;
    }

    let (progress_tx, progress_rx) = flume::unbounded::<(u32, u32)>();
    let render = gpui_tokio::Tokio::spawn(
        cx,
        crate::editor_export::render_for_share(project_path.clone(), progress_tx, cancel.clone()),
    );
    update(cx, &|job, _| job.stage = ShareStage::Rendering(0.));
    loop {
        let mut latest = None;
        while let Ok((rendered, total)) = progress_rx.try_recv() {
            latest = Some(rendered as f32 / total.max(1) as f32);
        }
        if let Some(progress) = latest {
            update(cx, &|job, _| {
                job.stage = ShareStage::Rendering(progress.min(1.))
            });
        }
        if progress_rx.is_disconnected() {
            break;
        }
        cx.background_executor()
            .timer(Duration::from_millis(100))
            .await;
    }
    let rendered = render
        .await
        .map_err(|error| error.to_string())
        .and_then(|result| result);
    if let Err(error) = rendered {
        if !cancel.load(Ordering::Acquire) {
            tracing::error!(error, "auto-share render failed");
            update(cx, &|job, _| job.stage = ShareStage::Failed(error.clone()));
        }
        return;
    }

    let (upload_tx, upload_rx) = flume::unbounded::<f64>();
    let upload = gpui_tokio::Tokio::spawn(
        cx,
        crate::upload::upload_exported_video(
            project_path.clone(),
            organization,
            move |progress| {
                let _ = upload_tx.send(progress);
            },
            cancel.clone(),
        ),
    );
    update(cx, &|job, _| job.stage = ShareStage::Uploading(0.));
    loop {
        let mut latest = None;
        while let Ok(progress) = upload_rx.try_recv() {
            latest = Some(progress as f32);
        }
        if let Some(progress) = latest {
            update(cx, &|job, _| {
                job.stage = ShareStage::Uploading(progress.clamp(0., 1.))
            });
        }
        if upload_rx.is_disconnected() {
            break;
        }
        cx.background_executor()
            .timer(Duration::from_millis(100))
            .await;
    }
    let uploaded = upload
        .await
        .map_err(|error| error.to_string())
        .and_then(|result| result);
    if cancel.load(Ordering::Acquire) {
        return;
    }
    match uploaded {
        Ok(UploadResult::Success(link)) => {
            crate::app_sounds::play_notification();
            update(cx, &|job, cx| {
                announce(job, &link, cx);
                job.stage = ShareStage::Done;
            });
        }
        Ok(other) => {
            let error = upload_failure(other).unwrap_or_default();
            update(cx, &|job, _| job.stage = ShareStage::Failed(error.clone()));
        }
        Err(error) => {
            tracing::error!(error, "auto-share upload failed");
            update(cx, &|job, _| job.stage = ShareStage::Failed(error.clone()));
        }
    }
}

/// Copies the link and opens it once per share, the way an Instant stop does.
fn announce(job: &mut ShareJob, link: &str, cx: &mut App) {
    job.link = Some(link.to_string());
    if job.link_announced {
        return;
    }
    job.link_announced = true;
    cx.write_to_clipboard(gpui::ClipboardItem::new_string(link.to_string()));
    if !crate::store::GeneralSettings::load().disable_auto_open_links {
        cx.open_url(link);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(stage: ShareStage) -> ShareJob {
        ShareJob {
            project_path: PathBuf::from("/tmp/recording.cap"),
            name: "Recording".into(),
            link: None,
            stage,
            link_announced: false,
            cancel: Arc::new(AtomicBool::new(false)),
            _task: None,
        }
    }

    #[test]
    fn progress_spans_render_then_upload_and_labels_say_which() {
        assert_eq!(job(ShareStage::Reserving).progress(), None);
        assert_eq!(job(ShareStage::Rendering(0.5)).progress(), Some(0.25));
        assert_eq!(job(ShareStage::Uploading(0.5)).progress(), Some(0.75));
        assert_eq!(
            job(ShareStage::Uploading(0.42)).status_label(),
            "Uploading… 42%"
        );
        assert!(job(ShareStage::Rendering(0.)).is_running());
        assert!(!job(ShareStage::Done).is_running());
        assert!(!job(ShareStage::Failed("offline".into())).is_running());
    }
}
