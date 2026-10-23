# Review the fresh camera evidence

**Current status:** both reviews are complete and frozen. The follow-up supplies two low-visibility controls; no further review is requested for these unreadable crops. Initial evaluation exposed one false absence, fixed in v10. All four target histories now abstain in development validation. Positive action accuracy still needs clearer source evidence. The instructions below document the completed review workflow.

Open [the local review page](../output/target-grounding-v9/human-review/review.html) in a browser. It contains five cases and 23 ordered frames from three new recordings of the supplied cameras. These recordings have not been sent to the action interpreter. Detector tracks and labels are suggestions, not verified physical identities.

1. Enter your reviewer name.
2. Watch each context video. Its playback time starts at zero; image captions show source-relative timestamps.
3. Describe the physical target. Check whether the crops follow the same actual object; choose `different` for a tracker identity switch and `unclear` when you cannot tell.
4. Mark whether the camera/scene reference supports a distance comparison. For each frame, select target visibility and apparent distance (`far`, `middle`, `near`, or `unclear`). Apply the same distance scale within one case. Do not force a transition: equal or unclear states are valid annotations.
5. In the untracked night case, inspect the whole frame. The repeated full-scene image below it is context, not a detector crop. Do not assume that the lack of a vehicle track means no vehicle exists.
6. Click **Save completed annotations**. Send the resulting `visn-state-annotations.json` back for evaluation, or provide its local path.

No fields contain model action predictions or suggested states. The page blocks incomplete submissions. All original evidence and packet hashes should remain unchanged. No annotations were saved by the automated browser check.

Validate a completed review locally:

```sh
.venv-retrieval/bin/python tools/prepare_state_review.py \
  --packet output/target-grounding-v9/human-review/packet.json \
  --check-annotations /Users/anagha/Downloads/visn-state-annotations.json
```

The validator checks reviewer metadata, every case/state/citation, and hashes for source recordings, context video, and frame/crop images. Human-review metadata is self-declared; the validator cannot establish the reviewer's independence. After a valid review is frozen, run acceptance inference against these same source windows and selected targets, keeping your state/identity labels out of prompts. Until then, no acceptance accuracy is reported.

This packet covers vehicle distance and tracker identity. It does not validate real-world door, placement, or boundary-crossing actions. Those need separately reviewed real footage. Existing synthetic reversal fixtures remain development diagnostics.

## Completed review and target-focused follow-up

Your original annotations are validated and frozen unchanged. Your correction is recorded separately: three entries describe the whole scene and are excluded from vehicle-action scoring. Both target-reviewed highway histories were marked as different objects across the sequence; both model interpretations abstained on identity. This passes two identity-safety checks but supplies no same-identity positive action score.

The remaining target-state review is [two car-crop cases with eight frames](../output/target-grounding-v9/target-review-followup/review.html). Orange boxes identify the selected vehicle. Choose `target` under **What did you review?**, describe the cropped vehicle, and label that vehicle's visibility and distance at each time. Use `different`/`unclear` and equal/unclear distance states when appropriate. All fields start blank; the earlier scene annotations are not prefilled.

Save the downloaded annotations and provide their local path. This follow-up uses a new packet hash and must be validated against `output/target-grounding-v9/target-review-followup/packet.json`. Its sources remain outside action inference pending this review. The original packet and original downloaded annotation file were not changed.

## Source-detail crop changes

Future packets use readable source-detail crops (no upscaling), with target selection based on crop size and observed geometry. The completed low-resolution packets are preserved. Re-evaluating those original labels requires the preview-crop control mode; the evaluator rejects changed crop resolutions. A development-only source-detail export exists for inspection, clearly labelled as previously evaluated footage. No additional review is requested for the source-crop implementation comparison.
