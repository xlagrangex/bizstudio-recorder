//! "Sources" export: the raw tracks of a Studio recording, unrendered, for
//! finishing in another editor.

use std::path::{Path, PathBuf};
use std::time::Duration;

use cap_enc_ffmpeg::aac::AACEncoder;
use cap_media_info::AudioInfo;
use cap_project::{RecordingMeta, StudioRecordingMeta};
use ffmpeg::{ChannelLayout, codec as avcodec, format as avformat};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SourceKind {
    Screen,
    Camera,
    Microphone,
    SystemAudio,
    Cursor,
    Keyboard,
}

impl SourceKind {
    pub const ALL: [Self; 6] = [
        Self::Screen,
        Self::Camera,
        Self::Microphone,
        Self::SystemAudio,
        Self::Cursor,
        Self::Keyboard,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Self::Screen => "Screen",
            Self::Camera => "Camera",
            Self::Microphone => "Microphone",
            Self::SystemAudio => "System audio",
            Self::Cursor => "Cursor data",
            Self::Keyboard => "Keyboard data",
        }
    }

    pub fn detail(self) -> &'static str {
        match self {
            Self::Screen => "screen.mp4",
            Self::Camera => "camera.mp4",
            Self::Microphone => "microphone.m4a",
            Self::SystemAudio => "system-audio.m4a",
            Self::Cursor => "cursor.json + cursor images",
            Self::Keyboard => "keyboard.bin",
        }
    }
}

#[derive(Debug, Default, Clone)]
struct SegmentFiles {
    display: Option<PathBuf>,
    camera: Option<PathBuf>,
    mic: Option<PathBuf>,
    system_audio: Option<PathBuf>,
    cursor: Option<PathBuf>,
    keyboard: Option<PathBuf>,
}

impl SegmentFiles {
    fn get(&self, kind: SourceKind) -> Option<&PathBuf> {
        match kind {
            SourceKind::Screen => self.display.as_ref(),
            SourceKind::Camera => self.camera.as_ref(),
            SourceKind::Microphone => self.mic.as_ref(),
            SourceKind::SystemAudio => self.system_audio.as_ref(),
            SourceKind::Cursor => self.cursor.as_ref(),
            SourceKind::Keyboard => self.keyboard.as_ref(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct RecordingSources {
    pub pretty_name: String,
    segments: Vec<SegmentFiles>,
    cursor_images: Vec<PathBuf>,
}

impl RecordingSources {
    pub fn load(project_path: &Path) -> Result<Self, String> {
        let meta = RecordingMeta::load_for_project(project_path)
            .map_err(|error| format!("Could not read the recording: {error}"))?;
        Self::from_meta(&meta).ok_or_else(|| "Only Studio recordings have sources".to_string())
    }

    fn from_meta(meta: &RecordingMeta) -> Option<Self> {
        let existing = |path: PathBuf| path.is_file().then_some(path);
        let segments = match meta.studio_meta()? {
            StudioRecordingMeta::SingleSegment { segment } => vec![SegmentFiles {
                display: existing(meta.path(&segment.display.path)),
                camera: segment
                    .camera
                    .as_ref()
                    .and_then(|camera| existing(meta.path(&camera.path))),
                mic: segment
                    .audio
                    .as_ref()
                    .and_then(|audio| existing(meta.path(&audio.path))),
                system_audio: None,
                cursor: segment
                    .cursor
                    .as_ref()
                    .and_then(|cursor| existing(meta.path(cursor))),
                keyboard: None,
            }],
            StudioRecordingMeta::MultipleSegments { inner } => inner
                .segments
                .iter()
                .map(|segment| SegmentFiles {
                    display: existing(meta.path(&segment.display.path)),
                    camera: segment
                        .camera
                        .as_ref()
                        .and_then(|camera| existing(meta.path(&camera.path))),
                    mic: segment
                        .mic
                        .as_ref()
                        .and_then(|mic| existing(meta.path(&mic.path))),
                    system_audio: segment
                        .system_audio
                        .as_ref()
                        .and_then(|audio| existing(meta.path(&audio.path))),
                    cursor: segment
                        .cursor
                        .as_ref()
                        .and_then(|cursor| existing(meta.path(cursor))),
                    keyboard: segment
                        .keyboard
                        .as_ref()
                        .and_then(|keyboard| existing(meta.path(keyboard))),
                })
                .collect(),
        };
        let mut cursor_images: Vec<PathBuf> =
            std::fs::read_dir(meta.project_path.join("content/cursors"))
                .into_iter()
                .flatten()
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| path.extension().is_some_and(|ext| ext == "png"))
                .collect();
        cursor_images.sort();
        Some(Self {
            pretty_name: meta.pretty_name.clone(),
            segments,
            cursor_images,
        })
    }

    /// The tracks this recording actually has, in checklist order.
    pub fn available(&self) -> Vec<SourceKind> {
        SourceKind::ALL
            .into_iter()
            .filter(|kind| !self.files(*kind).is_empty())
            .collect()
    }

    fn files(&self, kind: SourceKind) -> Vec<PathBuf> {
        self.segments
            .iter()
            .filter_map(|segment| segment.get(kind).cloned())
            .collect()
    }

    /// `~/Movies/Bizstudio/<recording name>/`, suffixed when a previous export
    /// already holds that name so nothing is overwritten.
    pub fn default_destination(&self) -> PathBuf {
        let root = dirs::video_dir()
            .or_else(|| dirs::home_dir().map(|home| home.join("Movies")))
            .unwrap_or_else(std::env::temp_dir)
            .join("Bizstudio");
        self.destination_in(&root)
    }

    pub fn destination_in(&self, parent: &Path) -> PathBuf {
        unique_dir(parent, &folder_name(&self.pretty_name))
    }

    /// Writes each chosen track into `destination`, reporting `(done, total)`
    /// after every track. Video is stream-copied into a plain MP4; audio is
    /// re-encoded to AAC because Opus has no place in an `.m4a`.
    pub fn export(
        &self,
        kinds: &[SourceKind],
        destination: &Path,
        progress: impl Fn(usize, usize),
    ) -> Result<PathBuf, String> {
        let kinds: Vec<SourceKind> = kinds
            .iter()
            .copied()
            .filter(|kind| !self.files(*kind).is_empty())
            .collect();
        if kinds.is_empty() {
            return Err("Nothing to export: pick at least one source.".into());
        }
        std::fs::create_dir_all(destination)
            .map_err(|error| format!("Could not create {}: {error}", destination.display()))?;
        progress(0, kinds.len());
        for (index, kind) in kinds.iter().enumerate() {
            self.export_kind(*kind, destination)
                .map_err(|error| format!("{}: {error}", kind.label()))?;
            progress(index + 1, kinds.len());
        }
        Ok(destination.to_path_buf())
    }

    fn export_kind(&self, kind: SourceKind, destination: &Path) -> Result<(), String> {
        let files = self.files(kind);
        match kind {
            SourceKind::Screen | SourceKind::Camera => {
                let output = destination.join(kind.detail());
                if let Err(error) =
                    cap_enc_ffmpeg::remux::concatenate_video_fragments(&files, &output)
                {
                    tracing::warn!(%error, kind = kind.label(), "stream copy failed; copying segments as recorded");
                    let _ = std::fs::remove_file(&output);
                    copy_numbered(&files, destination, kind.detail())?;
                }
                Ok(())
            }
            SourceKind::Microphone | SourceKind::SystemAudio => {
                let output = destination.join(kind.detail());
                if let Err(error) = concatenate_audio_to_aac(&files, &output) {
                    tracing::warn!(%error, kind = kind.label(), "AAC conversion failed; copying segments as recorded");
                    let _ = std::fs::remove_file(&output);
                    let stem = kind.detail().replace(".m4a", ".ogg");
                    copy_numbered(&files, destination, &stem)?;
                }
                Ok(())
            }
            SourceKind::Cursor => {
                copy_numbered(&files, destination, "cursor.json")?;
                if !self.cursor_images.is_empty() {
                    let images = destination.join("cursors");
                    std::fs::create_dir_all(&images).map_err(|error| error.to_string())?;
                    for image in &self.cursor_images {
                        if let Some(name) = image.file_name() {
                            copy(image, &images.join(name))?;
                        }
                    }
                }
                Ok(())
            }
            SourceKind::Keyboard => copy_numbered(&files, destination, "keyboard.bin"),
        }
    }
}

/// One file keeps `name`; several become `name-1`, `name-2`, … in segment
/// order, so a multi-part recording's pieces stay in sequence.
fn copy_numbered(files: &[PathBuf], destination: &Path, name: &str) -> Result<(), String> {
    if let [file] = files {
        return copy(file, &destination.join(name));
    }
    let (stem, ext) = name.rsplit_once('.').unwrap_or((name, ""));
    for (index, file) in files.iter().enumerate() {
        let numbered = if ext.is_empty() {
            format!("{stem}-{}", index + 1)
        } else {
            format!("{stem}-{}.{ext}", index + 1)
        };
        copy(file, &destination.join(numbered))?;
    }
    Ok(())
}

fn copy(from: &Path, to: &Path) -> Result<(), String> {
    std::fs::copy(from, to)
        .map(|_| ())
        .map_err(|error| format!("Could not copy {}: {error}", from.display()))
}

fn folder_name(pretty_name: &str) -> String {
    let name = sanitize_filename::sanitize_with_options(
        pretty_name,
        sanitize_filename::Options {
            windows: true,
            ..Default::default()
        },
    );
    let name = name.trim();
    if name.is_empty() {
        "Bizstudio Recording".into()
    } else {
        name.into()
    }
}

fn unique_dir(root: &Path, name: &str) -> PathBuf {
    let first = root.join(name);
    if !first.exists() {
        return first;
    }
    (2..)
        .map(|index| root.join(format!("{name} ({index})")))
        .find(|candidate| !candidate.exists())
        .unwrap_or(first)
}

/// Decodes each segment in order into one AAC track, so a recording that was
/// paused and resumed comes out as a single continuous file.
fn concatenate_audio_to_aac(files: &[PathBuf], output: &Path) -> Result<(), String> {
    let mut octx = avformat::output(output).map_err(|error| error.to_string())?;
    let mut encoder: Option<(AACEncoder, AudioInfo)> = None;
    let mut frame = ffmpeg::frame::Audio::empty();

    for file in files {
        let mut ictx = avformat::input(file).map_err(|error| error.to_string())?;
        let stream = ictx
            .streams()
            .best(ffmpeg::media::Type::Audio)
            .ok_or_else(|| format!("{} has no audio", file.display()))?;
        let stream_index = stream.index();
        let time_base = stream.time_base();
        let mut decoder = avcodec::Context::from_parameters(stream.parameters())
            .and_then(|context| context.decoder().audio())
            .map_err(|error| error.to_string())?;
        if decoder.channel_layout().is_empty() {
            decoder.set_channel_layout(ChannelLayout::default(decoder.channels() as i32));
        }
        decoder.set_packet_time_base(time_base);
        let info = AudioInfo::from_decoder(&decoder).map_err(|error| error.to_string())?;

        let (aac, first_info) = match &mut encoder {
            Some(existing) => existing,
            None => {
                let aac = AACEncoder::init_for_export(info, &mut octx)
                    .map_err(|error| error.to_string())?;
                octx.write_header().map_err(|error| error.to_string())?;
                encoder.insert((aac, info))
            }
        };
        if info != *first_info {
            return Err("segments were recorded with different audio formats".into());
        }

        let mut drain = |decoder: &mut ffmpeg::decoder::Audio,
                         aac: &mut AACEncoder,
                         octx: &mut avformat::context::Output|
         -> Result<(), String> {
            while decoder.receive_frame(&mut frame).is_ok() {
                aac.send_frame(frame.clone(), Duration::MAX, octx)
                    .map_err(|error| error.to_string())?;
            }
            Ok(())
        };
        for (stream, packet) in ictx.packets() {
            if stream.index() != stream_index {
                continue;
            }
            decoder
                .send_packet(&packet)
                .map_err(|error| error.to_string())?;
            drain(&mut decoder, aac, &mut octx)?;
        }
        decoder.send_eof().map_err(|error| error.to_string())?;
        drain(&mut decoder, aac, &mut octx)?;
    }

    let Some((mut aac, _)) = encoder else {
        return Err("no audio segments".into());
    };
    aac.flush(&mut octx).map_err(|error| error.to_string())?;
    octx.write_trailer().map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn several_segments_keep_their_order_in_numbered_names() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.json");
        let b = dir.path().join("b.json");
        std::fs::write(&a, "1").unwrap();
        std::fs::write(&b, "2").unwrap();
        let out = dir.path().join("out");
        std::fs::create_dir_all(&out).unwrap();

        copy_numbered(std::slice::from_ref(&a), &out, "cursor.json").unwrap();
        assert_eq!(
            std::fs::read_to_string(out.join("cursor.json")).unwrap(),
            "1"
        );

        copy_numbered(&[a, b], &out, "keyboard.bin").unwrap();
        assert_eq!(
            std::fs::read_to_string(out.join("keyboard-1.bin")).unwrap(),
            "1"
        );
        assert_eq!(
            std::fs::read_to_string(out.join("keyboard-2.bin")).unwrap(),
            "2"
        );
    }

    /// `CAP_GPUI_SOURCES_FIXTURE=<a .cap> cargo test --bin cap-gpui -- --ignored sources_export_real`
    #[test]
    #[ignore]
    fn sources_export_real_recording() {
        let Some(fixture) = std::env::var_os("CAP_GPUI_SOURCES_FIXTURE") else {
            return;
        };
        let sources = RecordingSources::load(Path::new(&fixture)).unwrap();
        let out = tempfile::tempdir().unwrap();
        let kinds = sources.available();
        sources.export(&kinds, out.path(), |_, _| {}).unwrap();
        for kind in kinds {
            if matches!(
                kind,
                SourceKind::Screen
                    | SourceKind::Camera
                    | SourceKind::Microphone
                    | SourceKind::SystemAudio
            ) {
                let file = out.path().join(kind.detail());
                assert!(
                    cap_enc_ffmpeg::remux::probe_media_valid(&file),
                    "{kind:?} not playable"
                );
                let duration = cap_enc_ffmpeg::remux::get_media_duration(&file).unwrap();
                eprintln!("{kind:?}: {duration:?}");
            }
        }
    }

    #[test]
    fn a_taken_folder_name_gets_a_suffix() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("Demo")).unwrap();
        assert_eq!(unique_dir(dir.path(), "Demo"), dir.path().join("Demo (2)"));
        assert_eq!(folder_name("a/b: c"), "ab c");
        assert_eq!(folder_name("  "), "Bizstudio Recording");
    }
}
