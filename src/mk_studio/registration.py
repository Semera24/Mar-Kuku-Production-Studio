"""Episode-neutral PNG alpha bounds and support-foot landmark calibration."""

from __future__ import annotations

import json
from pathlib import Path
from PIL import Image

from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.contracts import (
    AlphaBounds,
    AssetManifest,
    PoseRegistration,
    RegistrationCalibration,
)


def compute_alpha_bounds(image: Image.Image, threshold: int) -> AlphaBounds:
    """Compute bounding box [left, top, right, bottom] where alpha >= threshold."""
    if threshold < 0 or threshold > 255:
        raise ValueError(f"Alpha threshold must be in 0..255 (got {threshold})")
    if "A" not in image.getbands():
        return AlphaBounds(
            threshold=threshold,
            left=0,
            top=0,
            right=image.width,
            bottom=image.height,
        )
    alpha = image.getchannel("A")
    mask = alpha.point(lambda a: 255 if a >= threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return AlphaBounds(
            threshold=threshold,
            left=0,
            top=0,
            right=image.width,
            bottom=image.height,
        )
    left, top, right, bottom = bbox
    return AlphaBounds(
        threshold=threshold,
        left=int(left),
        top=int(top),
        right=int(right),
        bottom=int(bottom),
    )


def detect_pose_landmarks(
    image: Image.Image,
    bounds_t128: AlphaBounds,
    threshold: int = 128,
) -> dict[str, float]:
    """Detect head top and left/right/support foot contact coordinates at alpha >= threshold."""
    w, h = image.size
    if "A" not in image.getbands():
        return {
            "head_top_x": round(w / 2.0, 2),
            "head_top_y": 0.0,
            "left_foot_x": round(w * 0.4, 2),
            "left_foot_y": float(h - 1),
            "right_foot_x": round(w * 0.6, 2),
            "right_foot_y": float(h - 1),
            "support_foot_x": round(w / 2.0, 2),
            "support_foot_y": float(h - 1),
        }

    alpha = image.getchannel("A")
    px = alpha.load()
    assert px is not None
    left, top, right, bottom = (
        bounds_t128.left,
        bounds_t128.top,
        bounds_t128.right,
        bounds_t128.bottom,
    )

    top_xs = [x for x in range(left, right) if px[x, top] >= threshold]
    head_top_x = sum(top_xs) / len(top_xs) if top_xs else (left + right) / 2.0
    head_top_y = float(top)

    vis_h = max(1, bottom - top)
    mid_x = (left + right) / 2.0
    scan_top = max(top, int(bottom - max(20, vis_h * 0.12)))

    left_pts: list[tuple[int, int]] = []
    right_pts: list[tuple[int, int]] = []
    all_bottom_pts: list[tuple[int, int]] = []

    for y in range(scan_top, bottom):
        for x in range(left, right):
            if px[x, y] >= threshold:
                all_bottom_pts.append((x, y))
                if x < mid_x:
                    left_pts.append((x, y))
                else:
                    right_pts.append((x, y))

    def _contact_centroid(pts: list[tuple[int, int]], fallback_x: float) -> tuple[float, float]:
        if not pts:
            return round(fallback_x, 2), float(bottom - 1)
        max_y = max(p[1] for p in pts)
        band = [p[0] for p in pts if p[1] >= max_y - 3]
        cx = sum(band) / len(band) if band else fallback_x
        return round(cx, 2), float(max_y)

    if not left_pts and all_bottom_pts:
        left_pts = all_bottom_pts
    if not right_pts and all_bottom_pts:
        right_pts = all_bottom_pts

    lx, ly = _contact_centroid(left_pts, (left + mid_x) / 2.0)
    rx, ry = _contact_centroid(right_pts, (mid_x + right) / 2.0)

    support_x = round((lx + rx) / 2.0, 2)
    support_y = float(max(ly, ry))

    return {
        "head_top_x": round(head_top_x, 2),
        "head_top_y": head_top_y,
        "left_foot_x": lx,
        "left_foot_y": ly,
        "right_foot_x": rx,
        "right_foot_y": ry,
        "support_foot_x": support_x,
        "support_foot_y": support_y,
    }


def calibrate_pose_png(
    policy: RootPolicy,
    *,
    asset_id: str,
    character_or_prop: str,
    pose_name: str,
    path: str | Path,
    expected_sha256: str | None = None,
) -> PoseRegistration:
    """Inspect a PNG asset and return its calibrated PoseRegistration."""
    resolved = policy.resolve_read(path, expected_sha256=expected_sha256)
    digest = compute_sha256(resolved)

    with Image.open(resolved) as im:
        width, height = im.size
        has_alpha = "A" in im.getbands()
        b1 = compute_alpha_bounds(im, 1)
        b128 = compute_alpha_bounds(im, 128)
        b254 = compute_alpha_bounds(im, 254)
        landmarks = detect_pose_landmarks(im, b128, threshold=128)

    vis_height = float(max(1.0, landmarks["support_foot_y"] - landmarks["head_top_y"]))
    return PoseRegistration(
        asset_id=asset_id,
        character_or_prop=character_or_prop,
        pose_name=pose_name,
        path=str(resolved),
        sha256=digest,
        image_width=width,
        image_height=height,
        has_transparency=has_alpha,
        alpha_bounds_t1=b1,
        alpha_bounds_t128=b128,
        alpha_bounds_t254=b254,
        left_foot_x=landmarks["left_foot_x"],
        left_foot_y=landmarks["left_foot_y"],
        right_foot_x=landmarks["right_foot_x"],
        right_foot_y=landmarks["right_foot_y"],
        support_foot_x=landmarks["support_foot_x"],
        support_foot_y=landmarks["support_foot_y"],
        head_top_x=landmarks["head_top_x"],
        head_top_y=landmarks["head_top_y"],
        reference_visible_height_px=vis_height,
    )


def calibrate_manifest_assets(
    policy: RootPolicy,
    manifest: AssetManifest,
    *,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
    save_path: str | Path | None = None,
) -> RegistrationCalibration:
    """Calibrate all character_pose and prop assets in an AssetManifest."""
    poses: dict[str, PoseRegistration] = {}
    for asset_id, entry in manifest.assets.items():
        if entry.role not in ("character_pose", "prop"):
            continue
        reg = calibrate_pose_png(
            policy,
            asset_id=asset_id,
            character_or_prop=entry.character_id or entry.role,
            pose_name=entry.pose_name or asset_id,
            path=entry.path,
            expected_sha256=entry.sha256,
        )
        poses[asset_id] = reg

    calibration = RegistrationCalibration(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        poses=poses,
    )
    if save_path is not None:
        out = policy.resolve_output(save_path, overwrite=True, create_parents=True)
        out.write_text(json.dumps(calibration.model_dump(), indent=2) + "\n", encoding="utf-8")
    return calibration
