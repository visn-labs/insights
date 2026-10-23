//! Non-Negotiable Privacy Pipeline: Defeating Reconstruction Attacks at Millisecond Latency.
//!
//! Three-tier millisecond redaction architecture executed in volatile RAM:
//! - Tier 1: YuNet ultra-compact face detector (75k params, 1.6 ms latency) + SPD-Conv.
//! - Tier 2: Constant-velocity Kalman filter state per track with ByteTrack two-pass association,
//!   mask dilation, and 15-frame occlusion persistence to prevent transient PII leakage.
//! - Tier 3: Decoupled sanitization: Irreversible cryptographic zero-entropy erasure
//!   and 1-FPS asynchronous non-PII attribute extraction (NullFace / iFADIT style).

use serde::{Deserialize, Serialize};

/// Sanitization mode applied to redacted face/plate regions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SanitizationMode {
    /// Overwrites bounding polygon with zero-entropy noise / black mask.
    /// Mathematically non-invertible under GAN-Invert (zero mutual information).
    CryptographicErasure,
    /// Disentangled identity transformation preserving geometric head-pose and
    /// non-PII semantic tags (demographics/accessories) while purging identity.
    DisentangledAnonymization,
}

/// Raw detection emitted by the YuNet millisecond detector.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FaceDetection {
    /// Normalized [x, y, width, height]
    pub bbox: [f32; 4],
    pub confidence: f32,
    /// Five facial landmarks [right_eye, left_eye, nose_tip, mouth_right, mouth_left]
    pub landmarks: Option<[[f32; 2]; 5]>,
}

/// Non-PII behavioral and demographic attributes extracted by the Tier-3 pipeline.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct NonPiiAttributes {
    /// Disentangled demographic category (e.g., "adult", "senior", "adolescent")
    pub demographic_bracket: String,
    /// Detected accessories/headwear (S6 attribute category)
    pub accessories: Vec<String>,
    /// Head pose Euler angles: [yaw, pitch, roll] in degrees
    pub head_pose_degrees: [f32; 3],
    /// Estimated gaze direction unit vector [x, y, z]
    pub gaze_direction: [f32; 3],
}

/// Active redaction mask applied to a frame in volatile memory.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RedactionMask {
    pub mask_id: u64,
    /// Dilated bounding box [x, y, w, h] covering the face/plate region
    pub dilated_bbox: [f32; 4],
    pub confidence: f32,
    /// Number of consecutive frames this mask has persisted without a direct detection
    pub occlusion_frames: usize,
    /// True if projected via Kalman filter during temporary occlusion
    pub is_occluded_projection: bool,
    pub sanitization: SanitizationMode,
    pub attributes: Option<NonPiiAttributes>,
}

/// Configuration for the 3-Tier privacy redaction engine.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PrivacyConfig {
    /// Primary high confidence threshold for YuNet detections
    pub primary_threshold: f32,
    /// Secondary low confidence recovery threshold (ByteTrack 2-pass style)
    pub recovery_threshold: f32,
    /// Maximum consecutive frames to maintain redaction mask during total occlusion
    pub occlusion_grace_frames: usize,
    /// Percentage by which to dilate the bounding box (e.g. 0.20 = 20% expansion)
    pub mask_dilation_factor: f32,
    /// Target sanitization mode
    pub default_mode: SanitizationMode,
}

impl Default for PrivacyConfig {
    fn default() -> Self {
        Self {
            primary_threshold: 0.60,
            recovery_threshold: 0.10,
            occlusion_grace_frames: 15,
            mask_dilation_factor: 0.20,
            default_mode: SanitizationMode::CryptographicErasure,
        }
    }
}

/// Constant-velocity Kalman filter tracking a face bounding box in normalized coordinates.
#[derive(Clone, Debug)]
struct KalmanState {
    id: u64,
    // State vector: [x, y, w, h, vx, vy, vw, vh]
    state: [f32; 8],
    confidence: f32,
    occlusion_count: usize,
    mode: SanitizationMode,
    attributes: Option<NonPiiAttributes>,
}

impl KalmanState {
    fn new(id: u64, bbox: [f32; 4], confidence: f32, mode: SanitizationMode) -> Self {
        Self {
            id,
            state: [
                bbox[0], bbox[1], bbox[2], bbox[3], // position & size
                0.0, 0.0, 0.0, 0.0, // initial velocity
            ],
            confidence,
            occlusion_count: 0,
            mode,
            attributes: None,
        }
    }

    fn predict(&mut self) {
        // x += vx, y += vy, w += vw, h += vh
        self.state[0] += self.state[4];
        self.state[1] += self.state[5];
        self.state[2] += self.state[6];
        self.state[3] += self.state[7];
        self.occlusion_count += 1;
    }

    fn update(&mut self, bbox: [f32; 4], confidence: f32) {
        // Constant-velocity measurement residual with Kalman gain alpha=0.7, beta=0.3
        let alpha = 0.70;
        let beta = 0.30;

        let res_x = bbox[0] - self.state[0];
        let res_y = bbox[1] - self.state[1];
        let res_w = bbox[2] - self.state[2];
        let res_h = bbox[3] - self.state[3];

        self.state[0] += alpha * res_x;
        self.state[1] += alpha * res_y;
        self.state[2] += alpha * res_w;
        self.state[3] += alpha * res_h;

        self.state[4] += beta * res_x;
        self.state[5] += beta * res_y;
        self.state[6] += beta * res_w;
        self.state[7] += beta * res_h;

        self.confidence = confidence;
        self.occlusion_count = 0;
    }

    fn current_bbox(&self) -> [f32; 4] {
        [
            self.state[0].clamp(0.0, 1.0),
            self.state[1].clamp(0.0, 1.0),
            self.state[2].clamp(0.01, 1.0),
            self.state[3].clamp(0.01, 1.0),
        ]
    }
}

/// Compute Intersection over Union (IoU) of two bounding boxes.
pub fn box_iou(a: [f32; 4], b: [f32; 4]) -> f32 {
    let x1 = a[0].max(b[0]);
    let y1 = a[1].max(b[1]);
    let x2 = (a[0] + a[2]).min(b[0] + b[2]);
    let y2 = (a[1] + a[3]).min(b[1] + b[3]);

    let inter_w = (x2 - x1).max(0.0);
    let inter_h = (y2 - y1).max(0.0);
    let inter_area = inter_w * inter_h;

    let area_a = a[2] * a[3];
    let area_b = b[2] * b[3];
    let union_area = area_a + area_b - inter_area;

    if union_area <= 1e-9 {
        0.0
    } else {
        inter_area / union_area
    }
}

/// The stateful 3-Tier Privacy Redaction Manager.
pub struct PrivacyManager {
    config: PrivacyConfig,
    active_tracks: Vec<KalmanState>,
    next_id: u64,
}

impl PrivacyManager {
    pub fn new(config: PrivacyConfig) -> Self {
        Self {
            config,
            active_tracks: Vec::new(),
            next_id: 1,
        }
    }

    /// Processes YuNet detections for a single frame, updates Kalman persistence states,
    /// and generates conservative candidate masks. No image pixels are processed here.
    pub fn process_frame(&mut self, detections: &[FaceDetection]) -> Vec<RedactionMask> {
        // Step 1: Predict new positions for all active Kalman tracklets
        for track in &mut self.active_tracks {
            track.predict();
        }

        // Step 2: Two-pass association (ByteTrack style)
        // Pass 1: High confidence detections
        let mut high_dets = Vec::new();
        let mut low_dets = Vec::new();

        for det in detections {
            if det.confidence >= self.config.primary_threshold {
                high_dets.push(det);
            } else if det.confidence >= self.config.recovery_threshold {
                low_dets.push(det);
            }
        }

        let mut matched_track_indices = vec![false; self.active_tracks.len()];
        let mut matched_high_indices = vec![false; high_dets.len()];
        let mut matched_low_indices = vec![false; low_dets.len()];

        // Match high confidence detections
        for (d_idx, det) in high_dets.iter().enumerate() {
            let mut best_iou = 0.25f32; // Minimum matching IoU
            let mut best_t_idx = None;

            for (t_idx, track) in self.active_tracks.iter().enumerate() {
                if matched_track_indices[t_idx] {
                    continue;
                }
                let iou = box_iou(track.current_bbox(), det.bbox);
                if iou > best_iou {
                    best_iou = iou;
                    best_t_idx = Some(t_idx);
                }
            }

            if let Some(t_idx) = best_t_idx {
                self.active_tracks[t_idx].update(det.bbox, det.confidence);
                matched_track_indices[t_idx] = true;
                matched_high_indices[d_idx] = true;
            }
        }

        // Pass 2: Match remaining unmatched tracks against low confidence detections (recovering occluded/blurred faces)
        for (d_idx, det) in low_dets.iter().enumerate() {
            let mut best_iou = 0.20f32;
            let mut best_t_idx = None;

            for (t_idx, track) in self.active_tracks.iter().enumerate() {
                if matched_track_indices[t_idx] {
                    continue;
                }
                let iou = box_iou(track.current_bbox(), det.bbox);
                if iou > best_iou {
                    best_iou = iou;
                    best_t_idx = Some(t_idx);
                }
            }

            if let Some(t_idx) = best_t_idx {
                self.active_tracks[t_idx].update(det.bbox, det.confidence);
                matched_track_indices[t_idx] = true;
                matched_low_indices[d_idx] = true;
            }
        }

        // Step 3: Initialize new tracks for unmatched high confidence detections
        for (d_idx, det) in high_dets.iter().enumerate() {
            if !matched_high_indices[d_idx] {
                let id = self.next_id;
                self.next_id += 1;
                self.active_tracks.push(KalmanState::new(
                    id,
                    det.bbox,
                    det.confidence,
                    self.config.default_mode,
                ));
            }
        }

        // New uncertain detections still need coverage, even without an old track.
        for (d_idx, det) in low_dets.iter().enumerate() {
            if !matched_low_indices[d_idx] {
                let id = self.next_id;
                self.next_id += 1;
                self.active_tracks.push(KalmanState::new(
                    id,
                    det.bbox,
                    det.confidence,
                    self.config.default_mode,
                ));
            }
        }

        // Step 4: Prune tracks that exceed the occlusion grace window (e.g. >15 frames)
        let grace = self.config.occlusion_grace_frames;
        self.active_tracks
            .retain(|track| track.occlusion_count <= grace);

        // Step 5: Generate dilated masks for all active tracks
        let mut masks = Vec::new();
        let dilation = self.config.mask_dilation_factor;

        for track in &self.active_tracks {
            let [x, y, w, h] = track.current_bbox();
            let pad_w = w * dilation;
            let pad_h = h * dilation;

            let x0 = (x - pad_w / 2.0).clamp(0.0, 1.0);
            let y0 = (y - pad_h / 2.0).clamp(0.0, 1.0);
            let x1 = (x + w + pad_w / 2.0).clamp(x0, 1.0);
            let y1 = (y + h + pad_h / 2.0).clamp(y0, 1.0);
            let dilated = [x0, y0, x1 - x0, y1 - y0];

            masks.push(RedactionMask {
                mask_id: track.id,
                dilated_bbox: dilated,
                confidence: track.confidence,
                occlusion_frames: track.occlusion_count,
                is_occluded_projection: track.occlusion_count > 0,
                sanitization: track.mode,
                attributes: track.attributes.clone(),
            });
        }

        // Smoothed predictions may lag a measurement. Never expose its current extent.
        for det in detections
            .iter()
            .filter(|d| d.confidence >= self.config.recovery_threshold)
        {
            let [x, y, w, h] = det.bbox;
            let covered = masks.iter().any(|mask| {
                let [mx, my, mw, mh] = mask.dilated_bbox;
                mx <= x && my <= y && mx + mw >= x + w && my + mh >= y + h
            });
            if !covered {
                let x0 = (x - w * dilation / 2.0).clamp(0.0, 1.0);
                let y0 = (y - h * dilation / 2.0).clamp(0.0, 1.0);
                let x1 = (x + w * (1.0 + dilation / 2.0)).clamp(x0, 1.0);
                let y1 = (y + h * (1.0 + dilation / 2.0)).clamp(y0, 1.0);
                masks.push(RedactionMask {
                    mask_id: self.next_id,
                    dilated_bbox: [x0, y0, x1 - x0, y1 - y0],
                    confidence: det.confidence,
                    occlusion_frames: 0,
                    is_occluded_projection: false,
                    sanitization: self.config.default_mode,
                    attributes: None,
                });
                self.next_id += 1;
            }
        }

        masks
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dilates_mask_to_prevent_edge_leakage() {
        let mut manager = PrivacyManager::new(PrivacyConfig {
            mask_dilation_factor: 0.20,
            ..Default::default()
        });

        let detections = vec![FaceDetection {
            bbox: [0.4, 0.4, 0.1, 0.1],
            confidence: 0.85,
            landmarks: None,
        }];

        let masks = manager.process_frame(&detections);
        assert_eq!(masks.len(), 1);
        let mask = &masks[0];
        // Dilated box should be larger than original 0.1x0.1
        assert!(mask.dilated_bbox[2] > 0.10);
        assert!(mask.dilated_bbox[3] > 0.10);
        assert!(!mask.is_occluded_projection);
    }

    #[test]
    fn persists_redaction_through_occlusion_grace_window() {
        let mut manager = PrivacyManager::new(PrivacyConfig {
            occlusion_grace_frames: 5,
            ..Default::default()
        });

        // Frame 1: Clear detection
        let frame1 = vec![FaceDetection {
            bbox: [0.3, 0.3, 0.1, 0.1],
            confidence: 0.90,
            landmarks: None,
        }];
        manager.process_frame(&frame1);

        // Frame 2, 3, 4: Face turns away / occluded behind streetlight (0 detections)
        let empty: Vec<FaceDetection> = Vec::new();
        for frame_num in 2..=4 {
            let masks = manager.process_frame(&empty);
            assert_eq!(masks.len(), 1, "Mask must persist during frame {frame_num}");
            assert!(masks[0].is_occluded_projection);
            assert_eq!(masks[0].occlusion_frames, frame_num - 1);
        }

        // Frame 7: Exceeds 5-frame grace window
        manager.process_frame(&empty);
        manager.process_frame(&empty);
        let masks_after_grace = manager.process_frame(&empty);
        assert_eq!(
            masks_after_grace.len(),
            0,
            "Mask should expire after grace period"
        );
    }

    #[test]
    fn recovers_low_confidence_blurred_detection() {
        let mut manager = PrivacyManager::new(PrivacyConfig {
            primary_threshold: 0.60,
            recovery_threshold: 0.15,
            ..Default::default()
        });

        // Frame 1: High confidence face
        manager.process_frame(&[FaceDetection {
            bbox: [0.5, 0.5, 0.1, 0.1],
            confidence: 0.80,
            landmarks: None,
        }]);

        // Frame 2: Heavy motion blur drops detection score to 0.20
        let masks = manager.process_frame(&[FaceDetection {
            bbox: [0.505, 0.505, 0.1, 0.1],
            confidence: 0.20,
            landmarks: None,
        }]);

        assert_eq!(masks.len(), 1);
        assert_eq!(
            masks[0].occlusion_frames, 0,
            "Low-confidence detection was successfully recovered"
        );
    }
}
