# User Image Requirements

This document defines what image to send to the Ghibli QR API to consistently get the best result. It applies to all integrating clients: Mobile, Web, Backend, and QA.

---

## Supported Image Types

- Selfie
- Portrait
- Half Body
- Full Body

---

## Supported File Formats

- JPEG
- JPG
- PNG

---

## Recommended Image

| Item | Recommendation |
| --- | --- |
| Resolution | 1080 px or higher |
| Aspect Ratio | Keep the original image ratio |
| File Size | Maximum 10 MB |
| Image Quality | High quality |

---

## Subject Requirements

### Required

- Exactly one real person
- Face clearly visible
- Full head visible

### Recommended

- Subject centered
- Chest-up framing
- Full body visible when using Full Body photos

---

## Image Quality

✓ Good lighting
✓ Sharp image
✓ Minimal motion blur
✓ Natural colors
✓ Avoid heavy beauty filters
✓ Avoid strong backlighting

---

## Unsupported Images

✗ Multiple people
✗ Cartoons
✗ Anime
✗ Animals
✗ Face fully hidden
✗ Extremely blurry images

---

## Recommended Client-side Validation

- File type
- File size (Maximum 10 MB)
- Image resolution
- Single person
- Face visibility
- Image sharpness

---

## Backend Processing

- Validates the uploaded image.
- Prepares the image for AI generation.
- Preserves the original aspect ratio.
- Returns the generated image in high resolution.

---

## Best Results

✓ Use a recent photo.
✓ Keep the face clearly visible.
✓ Use good lighting.
✓ Avoid heavy filters.
✓ Submit only one person.

---

> Images larger than 10 MB should be rejected by the client before upload to improve performance and reduce unnecessary API requests.
