//! The raw tracks of a shared Studio recording, sent next to its rendered
//! video under `sources/` so the web dashboard can offer them as downloads.
//!
//! Best-effort and detached: the share link never waits on it. The manifest
//! goes up last and the web treats sources as present only once it exists, so
//! an interrupted run leaves nothing half-listed and the next share retries.

use std::path::{Path, PathBuf};
use std::time::Duration;

use futures_util::stream;
use serde::Serialize;
use tokio::io::AsyncReadExt as _;

use super::{SignedUploadTarget, signed_put_target, upload_client, upload_content_type};
use crate::auth::{self, AuthApiError};
use crate::source_export::{RecordingSources, SourceKind};

const MEDIA_KINDS: [SourceKind; 4] = [
    SourceKind::Screen,
    SourceKind::Camera,
    SourceKind::Microphone,
    SourceKind::SystemAudio,
];
const MANIFEST: &str = "sources/manifest.json";
const SOURCE_UPLOAD_TIMEOUT: Duration = Duration::from_secs(60 * 60);
const READ_CHUNK: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ManifestFile {
    name: String,
    kind: &'static str,
    label: &'static str,
    size: u64,
}

#[derive(Serialize)]
struct Manifest<'a> {
    version: u32,
    files: &'a [ManifestFile],
}

pub(super) fn spawn_sources_upload(project_path: PathBuf, video_id: String) {
    // The upload tests drive mock servers that count every request.
    if cfg!(test) {
        return;
    }
    let Ok(handle) = tokio::runtime::Handle::try_current() else {
        return;
    };
    let context = auth::UploadRequestContext::current();
    handle.spawn(async move {
        let run = upload_sources(project_path, video_id.clone());
        let result = match context {
            Some(context) => context.run(run).await,
            None => run.await,
        };
        match result {
            Ok(true) => tracing::info!(video_id, "uploaded recording sources"),
            Ok(false) => tracing::debug!(video_id, "no sources to upload"),
            Err(error) => tracing::warn!(video_id, %error, "sources upload failed"),
        }
    });
}

async fn upload_sources(project_path: PathBuf, video_id: String) -> Result<bool, String> {
    let marker = marker_path(&project_path, &video_id);
    if marker.exists() {
        return Ok(false);
    }
    let staging = std::env::temp_dir().join(format!("bizrec-sources-{video_id}"));
    let result = upload_staged(&project_path, &video_id, &staging, &marker).await;
    let _ = std::fs::remove_dir_all(&staging);
    result
}

async fn upload_staged(
    project_path: &Path,
    video_id: &str,
    staging: &Path,
    marker: &Path,
) -> Result<bool, String> {
    let files = {
        let project_path = project_path.to_path_buf();
        let staging = staging.to_path_buf();
        tokio::task::spawn_blocking(move || stage_sources(&project_path, &staging))
            .await
            .map_err(|error| error.to_string())??
    };
    if files.is_empty() {
        return Ok(false);
    }
    for file in &files {
        put_file(
            video_id,
            &staging.join(&file.name),
            &format!("sources/{}", file.name),
        )
        .await
        .map_err(|error| format!("{}: {error}", file.name))?;
    }
    super::presigned_put_bytes(video_id, MANIFEST, manifest_json(&files))
        .await
        .map_err(|error| format!("manifest: {error}"))?;
    std::fs::write(marker, b"").map_err(|error| format!("marker: {error}"))?;
    Ok(true)
}

fn stage_sources(project_path: &Path, staging: &Path) -> Result<Vec<ManifestFile>, String> {
    let Ok(sources) = RecordingSources::load(project_path) else {
        return Ok(Vec::new());
    };
    let kinds: Vec<SourceKind> = sources
        .available()
        .into_iter()
        .filter(|kind| MEDIA_KINDS.contains(kind))
        .collect();
    if kinds.is_empty() {
        return Ok(Vec::new());
    }
    let _ = std::fs::remove_dir_all(staging);
    sources.export(&kinds, staging, |_, _| {})?;
    let mut files = Vec::new();
    for entry in std::fs::read_dir(staging).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let metadata = entry.metadata().map_err(|error| error.to_string())?;
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if !metadata.is_file() {
            continue;
        }
        if let Some(kind) = kind_for_file(&name) {
            files.push(ManifestFile {
                name,
                kind: kind_slug(kind),
                label: kind.label(),
                size: metadata.len(),
            });
        }
    }
    sort_files(&mut files);
    Ok(files)
}

/// Matches `source_export`'s names: `screen.mp4`, numbered `screen-2.mp4`, and
/// the `.ogg` fallback when an audio track could not be converted.
fn kind_for_file(name: &str) -> Option<SourceKind> {
    MEDIA_KINDS.into_iter().find(|kind| {
        let stem = kind.detail().split('.').next().unwrap_or_default();
        name.starts_with(&format!("{stem}.")) || name.starts_with(&format!("{stem}-"))
    })
}

fn kind_slug(kind: SourceKind) -> &'static str {
    match kind {
        SourceKind::Screen => "screen",
        SourceKind::Camera => "camera",
        SourceKind::Microphone => "microphone",
        SourceKind::SystemAudio => "system-audio",
        SourceKind::Cursor => "cursor",
        SourceKind::Keyboard => "keyboard",
    }
}

fn sort_files(files: &mut [ManifestFile]) {
    let rank = |slug: &str| {
        MEDIA_KINDS
            .iter()
            .position(|kind| kind_slug(*kind) == slug)
            .unwrap_or(MEDIA_KINDS.len())
    };
    files.sort_by(|a, b| rank(a.kind).cmp(&rank(b.kind)).then(a.name.cmp(&b.name)));
}

fn manifest_json(files: &[ManifestFile]) -> Vec<u8> {
    serde_json::to_vec(&Manifest { version: 1, files }).unwrap_or_default()
}

fn marker_path(project_path: &Path, video_id: &str) -> PathBuf {
    let id: String = video_id
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect();
    project_path.join(format!("sources-uploaded-{id}"))
}

/// Streamed rather than read into memory: a screen track runs to gigabytes.
async fn put_file(video_id: &str, path: &Path, subpath: &str) -> Result<(), AuthApiError> {
    let target: SignedUploadTarget = signed_put_target(video_id, subpath, None).await?;
    let file = tokio::fs::File::open(path)
        .await
        .map_err(|error| AuthApiError::Other(error.to_string()))?;
    let length = file
        .metadata()
        .await
        .map_err(|error| AuthApiError::Other(error.to_string()))?
        .len();
    let body = stream::unfold(file, |mut file| async move {
        let mut buffer = vec![0; READ_CHUNK];
        match file.read(&mut buffer).await {
            Ok(0) => None,
            Ok(read) => {
                buffer.truncate(read);
                Some((Ok::<_, std::io::Error>(bytes::Bytes::from(buffer)), file))
            }
            Err(error) => Some((Err(error), file)),
        }
    });
    let mut request = upload_client()
        .put(&target.url)
        .header("Content-Length", length)
        .header("Content-Type", upload_content_type(subpath))
        .timeout(SOURCE_UPLOAD_TIMEOUT)
        .body(reqwest::Body::wrap_stream(body));
    if super::is_google_drive_resumable_url(&target.url) && length > 0 {
        request = request.header(
            "Content-Range",
            format!("bytes 0-{}/{}", length.saturating_sub(1), length),
        );
    }
    for (name, value) in target.headers {
        request = request.header(name, value);
    }
    let response = request.send().await.map_err(|error| {
        if error.is_timeout() {
            AuthApiError::Timeout
        } else {
            AuthApiError::Other(error.without_url().to_string())
        }
    })?;
    let status = response.status();
    if !status.is_success() {
        return Err(AuthApiError::Status(
            status,
            format!("upload failed: {status}"),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn file(name: &str, kind: SourceKind, size: u64) -> ManifestFile {
        ManifestFile {
            name: name.into(),
            kind: kind_slug(kind),
            label: kind.label(),
            size,
        }
    }

    #[test]
    fn exported_names_map_to_their_track() {
        assert_eq!(kind_for_file("screen.mp4"), Some(SourceKind::Screen));
        assert_eq!(kind_for_file("screen-2.mp4"), Some(SourceKind::Screen));
        assert_eq!(kind_for_file("camera.mp4"), Some(SourceKind::Camera));
        assert_eq!(
            kind_for_file("microphone-1.ogg"),
            Some(SourceKind::Microphone)
        );
        assert_eq!(
            kind_for_file("system-audio.m4a"),
            Some(SourceKind::SystemAudio)
        );
        assert_eq!(kind_for_file("cursor.json"), None);
        assert_eq!(kind_for_file(".DS_Store"), None);
    }

    #[test]
    fn manifest_lists_tracks_in_checklist_order() {
        let mut files = vec![
            file("system-audio.m4a", SourceKind::SystemAudio, 3),
            file("screen-2.mp4", SourceKind::Screen, 2),
            file("camera.mp4", SourceKind::Camera, 4),
            file("screen-1.mp4", SourceKind::Screen, 1),
        ];
        sort_files(&mut files);
        let json: serde_json::Value = serde_json::from_slice(&manifest_json(&files)).unwrap();
        assert_eq!(
            json,
            serde_json::json!({
                "version": 1,
                "files": [
                    {"name": "screen-1.mp4", "kind": "screen", "label": "Screen", "size": 1},
                    {"name": "screen-2.mp4", "kind": "screen", "label": "Screen", "size": 2},
                    {"name": "camera.mp4", "kind": "camera", "label": "Camera", "size": 4},
                    {"name": "system-audio.m4a", "kind": "system-audio", "label": "System audio", "size": 3},
                ]
            })
        );
    }

    #[test]
    fn marker_is_per_video_and_path_safe() {
        let project = Path::new("/tmp/rec.cap");
        assert_eq!(
            marker_path(project, "abc123"),
            project.join("sources-uploaded-abc123")
        );
        assert_eq!(
            marker_path(project, "../x"),
            project.join("sources-uploaded-x")
        );
    }

    #[tokio::test]
    async fn an_uploaded_video_is_not_sent_again() {
        let project =
            std::env::temp_dir().join(format!("bizrec-sources-test-{}", std::process::id()));
        std::fs::create_dir_all(&project).unwrap();
        std::fs::write(marker_path(&project, "vid1"), b"").unwrap();
        assert_eq!(
            upload_sources(project.clone(), "vid1".into()).await,
            Ok(false)
        );
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn instant_or_unreadable_projects_stage_nothing() {
        let staging = std::env::temp_dir().join("bizrec-sources-test-none");
        assert_eq!(
            stage_sources(Path::new("/nonexistent/project.cap"), &staging),
            Ok(Vec::new())
        );
    }
}
