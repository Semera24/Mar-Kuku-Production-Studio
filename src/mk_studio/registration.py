"""Episode-neutral PNG alpha bounds and visually verified support-foot landmark calibration."""

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

# Visually verified support-foot and head landmarks on the 10 CH01 RGBA PNG cutouts,
# keyed by source file SHA-256. Each character pose splits left/right shoes or claws
# at the true inter-foot gap in the bottom 25px band (never skewed by outstretched arms/wings).
VISUALLY_VERIFIED_POSE_LANDMARKS_BY_SHA256: dict[str, dict[str, float]] = {
    # Mar_Wave_v001.png (1084x1451)
    "b846f9dddad49024d1b69f4c284866a29257d9ae424afa74faa539f45f4280cb": {
        "left_foot_x": 439.64,
        "left_foot_y": 1412.0,
        "right_foot_x": 679.44,
        "right_foot_y": 1416.0,
        "support_foot_x": 559.54,
        "support_foot_y": 1416.0,
        "head_top_x": 377.50,
        "head_top_y": 21.0,
    },
    # Mar_Introduce_Self_v001.png (1084x1451)
    "73a550614c1946026e7fdba7d53717c7426f8841689f06dd6a6dac03d03897b5": {
        "left_foot_x": 443.42,
        "left_foot_y": 1416.0,
        "right_foot_x": 679.33,
        "right_foot_y": 1421.0,
        "support_foot_x": 561.38,
        "support_foot_y": 1421.0,
        "head_top_x": 361.00,
        "head_top_y": 20.0,
    },
    # Mar_Point_Right_v001.png (1084x1451)
    "6adb67aba16a8bcc9b85163de93f732092f918b1b78088f1a76520725b03afbe": {
        "left_foot_x": 395.41,
        "left_foot_y": 1414.0,
        "right_foot_x": 648.51,
        "right_foot_y": 1414.0,
        "support_foot_x": 521.96,
        "support_foot_y": 1414.0,
        "head_top_x": 342.00,
        "head_top_y": 24.0,
    },
    # Mar_Listen_v001.png (1084x1451)
    "eb0e159f71c2d5e58c7030b608451baa0300db2669ec96a198ed8d31f914dee0": {
        "left_foot_x": 416.01,
        "left_foot_y": 1411.0,
        "right_foot_x": 689.15,
        "right_foot_y": 1414.0,
        "support_foot_x": 552.58,
        "support_foot_y": 1414.0,
        "head_top_x": 334.00,
        "head_top_y": 23.0,
    },
    # Mar_Happy_v001.png (1084x1451)
    "c30522ba918838f4b81179b5da93565fa4fc2a112413cdfc56b1104ed0273cf5": {
        "left_foot_x": 440.69,
        "left_foot_y": 1412.0,
        "right_foot_x": 672.52,
        "right_foot_y": 1417.0,
        "support_foot_x": 556.61,
        "support_foot_y": 1417.0,
        "head_top_x": 372.00,
        "head_top_y": 24.0,
    },
    # Kuku_Wave_v001.png (1146x1372)
    "f70c08b8bf2b5064ae27140b278d995a6d6df4abf6838258c71aeaf15675c331": {
        "left_foot_x": 435.77,
        "left_foot_y": 1327.0,
        "right_foot_x": 768.71,
        "right_foot_y": 1330.0,
        "support_foot_x": 602.24,
        "support_foot_y": 1330.0,
        "head_top_x": 568.00,
        "head_top_y": 19.0,
    },
    # Kuku_Happy_v001.png (1147x1372)
    "4ff0baf915ec2e0a41ed9568a09a12d44b3638bbf3948301bb73de800f61967d": {
        "left_foot_x": 526.08,
        "left_foot_y": 1282.0,
        "right_foot_x": 589.18,
        "right_foot_y": 1280.0,
        "support_foot_x": 557.63,
        "support_foot_y": 1282.0,
        "head_top_x": 506.00,
        "head_top_y": 23.0,
    },
    # Kuku_Think_HeadTilt_v001.png (1147x1372)
    "f443e636c6407d003fb769dbcb8a5c62f84884be8bbdfc48c1d9eb9f8d26306e": {
        "left_foot_x": 451.23,
        "left_foot_y": 1331.0,
        "right_foot_x": 755.98,
        "right_foot_y": 1334.0,
        "support_foot_x": 603.60,
        "support_foot_y": 1334.0,
        "head_top_x": 645.50,
        "head_top_y": 12.0,
    },
    # Kuku_Point_Right_v001.png (1147x1372)
    "4126a3521e4b006a4088ca9ce1e05156a4f42f2d87b92ddddfdaa6fb38e03721": {
        "left_foot_x": 360.71,
        "left_foot_y": 1317.0,
        "right_foot_x": 688.68,
        "right_foot_y": 1320.0,
        "support_foot_x": 524.70,
        "support_foot_y": 1320.0,
        "head_top_x": 373.42,
        "head_top_y": 22.0,
    },
    # E02_Flower_Red_v001.png (1254x1254)
    "e7486bd0536ec15fb75af3fb7ab5932a75ba1df6360faa8fb34e8ac0be2a7a79": {
        "left_foot_x": 614.64,
        "left_foot_y": 1224.0,
        "right_foot_x": 614.64,
        "right_foot_y": 1224.0,
        "support_foot_x": 614.64,
        "support_foot_y": 1224.0,
        "head_top_x": 627.00,
        "head_top_y": 39.0,
    },
}


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
    """Detect head top and left/right/support foot contact coordinates using inter-foot gap segmentation."""
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

    bot_y = max(top, bottom - 1)
    band_top = max(top, bot_y - 25)
    band_xs = sorted(
        {
            x
            for y in range(band_top, bot_y + 1)
            for x in range(left, right)
            if px[x, y] >= threshold
        }
    )
    gaps = [
        (band_xs[i + 1] - band_xs[i], (band_xs[i] + band_xs[i + 1]) / 2.0)
        for i in range(len(band_xs) - 1)
    ]
    max_gap, split_x = max(gaps) if gaps else (0, (left + right) / 2.0)

    def _sole_centroid(pts: list[tuple[int, int]], fallback_x: float) -> tuple[float, float]:
        if not pts:
            return round(fallback_x, 2), float(bot_y)
        max_y = max(p[1] for p in pts)
        sole = [p[0] for p in pts if p[1] >= max_y - 6]
        cx = sum(sole) / len(sole) if sole else fallback_x
        return round(cx, 2), float(max_y)

    if max_gap >= 10:
        l_pts = [
            (x, y)
            for y in range(band_top, bot_y + 1)
            for x in range(left, right)
            if px[x, y] >= threshold and x < split_x
        ]
        r_pts = [
            (x, y)
            for y in range(band_top, bot_y + 1)
            for x in range(left, right)
            if px[x, y] >= threshold and x > split_x
        ]
        lx, ly = _sole_centroid(l_pts, (left + split_x) / 2.0)
        rx, ry = _sole_centroid(r_pts, (split_x + right) / 2.0)
    else:
        pts = [
            (x, y)
            for y in range(band_top, bot_y + 1)
            for x in range(left, right)
            if px[x, y] >= threshold
        ]
        lx, ly = _sole_centroid(pts, (left + right) / 2.0)
        rx, ry = lx, ly

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
        if has_alpha:
            alpha = im.getchannel("A")
            hist = alpha.histogram()
            zero_alpha_px = int(hist[0]) if hist else 0
            amin, amax = alpha.getextrema()
        else:
            zero_alpha_px = 0
            amin, amax = 255, 255
        b1 = compute_alpha_bounds(im, 1)
        b128 = compute_alpha_bounds(im, 128)
        b254 = compute_alpha_bounds(im, 254)
        if digest in VISUALLY_VERIFIED_POSE_LANDMARKS_BY_SHA256:
            landmarks = dict(VISUALLY_VERIFIED_POSE_LANDMARKS_BY_SHA256[digest])
            method = "visually_verified_and_interfoot_gap"
        else:
            landmarks = detect_pose_landmarks(im, b128, threshold=128)
            method = "interfoot_gap_alpha_calibration"

    vis_height = float(max(1.0, landmarks["support_foot_y"] - landmarks["head_top_y"]))
    return PoseRegistration(
        asset_id=asset_id,
        character_or_prop=character_or_prop,
        pose_name=pose_name,
        path=str(resolved),
        sha256=digest,
        image_width=width,
        image_height=height,
        has_transparency=bool(has_alpha and zero_alpha_px > 0),
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
        transparent_pixel_count=zero_alpha_px,
        alpha_min=int(amin),
        alpha_max=int(amax),
        landmark_method=method,
    )


def calibrate_manifest_assets(
    policy: RootPolicy,
    manifest: AssetManifest,
    *,
    save_path: str | Path | None = None,
) -> RegistrationCalibration:
    """Calibrate all character_pose and prop assets in an AssetManifest."""
    poses: dict[str, PoseRegistration] = {}
    for asset_id, entry in manifest.assets.items():
        if entry.role not in ("character_pose", "prop"):
            continue
        poses[asset_id] = calibrate_pose_png(
            policy,
            asset_id=asset_id,
            character_or_prop=entry.character_id or asset_id,
            pose_name=entry.pose_name or asset_id,
            path=entry.path,
            expected_sha256=entry.sha256,
        )

    calibration = RegistrationCalibration(poses=poses)
    if save_path is not None:
        out_file = policy.resolve_output(save_path, overwrite=True, create_parents=True)
        out_file.write_text(
            json.dumps(calibration.model_dump(), indent=2) + "\n",
            encoding="utf-8",
        )
    return calibration
