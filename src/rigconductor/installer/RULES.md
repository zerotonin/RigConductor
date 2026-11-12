# Labelling rules: Drosophila triads

Open the package with `label.sh` (or the "SLEAP labelling" icon, then
File → Open → `labels.pkg.slp`). Every row of the **Labeling Suggestions** tab is
one frame to label; **Space** goes to the next one. Save often with **Ctrl+S**.

## Per frame

- **3 flies**: two intact males and the decapitated female.
- The **female** gets head, eyeL and eyeR placed where the head would be, and those
  three points marked **not visible** (right-click the point → toggle visibility).
  Do not delete them.
- Where a frame was pre-filled by the model, **double-click** each guessed fly to
  turn it into a labelled fly, then **check every point** and correct it. Delete
  guesses that are not a fly (select, **Ctrl+Backspace**).
- **Finish a frame completely or skip it.** A frame with some flies corrected and
  some still predicted is rejected when it comes back.
- If a fly is not in the image or you cannot tell the flies apart, skip the frame.

## Points (13 per fly, flies13 order)

head, thorax, abdomen, wingL, wingR, forelegL4, forelegR4, midlegL4, midlegR4,
hindlegL4, hindlegR4 (leg tips), eyeL, eyeR.

- **Thorax** is the anchor: be precise, every fly needs it.
- Left/right are the **fly's** left and right.
- Leg tips you cannot see: leave them out (do not guess).

## When you are done

Save, close SLEAP, and bring the stick back. Do not rename or move
`labels.pkg.slp`; the Conductor recognises every frame by its pixels and rejects
frames that are not part of this package.
