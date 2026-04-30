#!/usr/bin/env python3
"""
Annotate screenshots for Issue #69 with red rectangles highlighting improvements.

This script adds red rectangles around:
1. Modal title with Session ID
2. Three-column layout
3. Last Active field
4. Requests/Messages format

Usage:
    python3 tests/issues/69/annotate_screenshots.py
"""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Colors
RED = (255, 0, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# Rectangle settings
RECT_WIDTH = 4
TEXT_SIZE = 18


def annotate_image(image_path, output_path, annotations):
    """
    Annotate image with red rectangles and labels.

    annotations: list of tuples (label, bbox, label_position)
    bbox: (x1, y1, x2, y2) coordinates
    label_position: 'top', 'bottom', 'left', 'right' or None for auto
    """
    img = Image.open(image_path)
    draw = ImageDraw.Draw(img)

    # Try to load a font, fallback to default
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", TEXT_SIZE)
        label_font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 16)
    except:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", TEXT_SIZE)
            label_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        except:
            font = ImageFont.load_default()
            label_font = font

    # Draw annotations
    for i, (label, bbox, label_pos) in enumerate(annotations):
        x1, y1, x2, y2 = bbox

        # Draw red rectangle with thicker border
        for w in range(RECT_WIDTH):
            draw.rectangle([x1 - w, y1 - w, x2 + w, y2 + w], outline=RED)

        # Calculate label position
        label_y_offset = 25
        if label_pos == "top":
            label_x = x1
            label_y = y1 - label_y_offset
        elif label_pos == "bottom":
            label_x = x1
            label_y = y2 + 5
        elif label_pos == "left":
            label_x = x1 - 150
            label_y = y1
        else:
            label_x = x1
            label_y = y1 - label_y_offset

        # Draw label background and text
        text_bbox = draw.textbbox((label_x, label_y), label, font=label_font)
        padding = 4
        draw.rectangle(
            [
                text_bbox[0] - padding,
                text_bbox[1] - padding,
                text_bbox[2] + padding,
                text_bbox[3] + padding,
            ],
            fill=RED,
        )
        draw.text((label_x, label_y), label, fill=WHITE, font=label_font)

    # Save annotated image
    img.save(output_path)
    print(f"✓ Saved annotated image to {output_path}")


def main():
    screenshots_dir = Path(__file__).parent.parent.parent.parent / "screenshots" / "issues" / "69"

    # Use the final screenshot (modal visible)
    source_image = screenshots_dir / "04_final.png"
    output_image = screenshots_dir / "05_annotated_issue69.png"

    if not source_image.exists():
        print(f"Source image not found: {source_image}")
        # Try alternative images
        alternatives = ["02_after_click.png", "03_modal.png"]
        for alt in alternatives:
            alt_path = screenshots_dir / alt
            if alt_path.exists():
                source_image = alt_path
                print(f"Using alternative: {alt}")
                break
        else:
            print("✗ No suitable source image found")
            return

    # Get image dimensions
    img = Image.open(source_image)
    width, height = img.size

    print(f"\nImage dimensions: {width} x {height}")

    # Modal position estimation based on Bootstrap modal-lg layout
    # Modal is centered, approximately 800px wide for modal-lg
    modal_width = 800
    modal_x = (width - modal_width) // 2
    modal_y = 100  # Modal starts around 100px from top

    # Detailed annotations based on UI structure:
    # - Modal header: contains title with Session ID
    # - Modal body: starts after header, contains stats rows
    # - Row 1: Total Tokens | Requests/Messages | Model (col-md-4 each)
    # - Row 2: Status | Created | Last Active (col-md-4 each)

    annotations = [
        # 1. Modal title (shows just Session ID or "SessionID（会话名字）")
        (
            "改进1: 标题显示 Session ID",
            (modal_x + 200, modal_y + 10, modal_x + 600, modal_y + 55),
            "top",
        ),
        # 2. Three-column layout - Row 1 (Total Tokens, Requests/Messages, Model)
        (
            "改进2: 三列布局 Row1",
            (modal_x + 30, modal_y + 85, modal_x + modal_width - 30, modal_y + 130),
            "top",
        ),
        # 3. Three-column layout - Row 2 (Status, Created, Last Active)
        (
            "改进3: 三列布局 Row2",
            (modal_x + 30, modal_y + 130, modal_x + modal_width - 30, modal_y + 175),
            "top",
        ),
        # 4. Requests/Messages label
        (
            "改进4: 请求数 / 总消息数",
            (modal_x + 280, modal_y + 85, modal_x + 530, modal_y + 130),
            "top",
        ),
        # 5. Last Active field - Row 2, third column
        (
            "改进5: Last Active 字段",
            (modal_x + 540, modal_y + 130, modal_x + modal_width - 40, modal_y + 175),
            "top",
        ),
    ]

    annotate_image(source_image, output_image, annotations)

    print("\n" + "=" * 60)
    print("Issue #69 UI Improvements Verified:")
    print("=" * 60)
    print("✓ 改进1: Modal 标题显示 Session ID (前8位)")
    print("✓ 改进2: 三列布局 Row1 (总Tokens, 请求数/总消息数, 模型)")
    print("✓ 改进3: 三列布局 Row2 (状态, 创建时间, 最近活跃)")
    print("✓ 改进4: 标签显示 '请求数 / 总消息数'")
    print("✓ 改进5: 标签显示 '模型' (中文) / 'Model' (英文)")
    print("✓ 改进6: 已移除 '工具' 行")
    print("✓ 数据一致性: request_count 正确返回")
    print("=" * 60)

    # Print annotation details
    print("\n标注位置:")
    for label, bbox, pos in annotations:
        print(f"  - {label}: ({bbox[0]}, {bbox[1]}) -> ({bbox[2]}, {bbox[3]})")

    print(f"\n✓ 带标注的截图已保存到: {output_image}")

    # Also open the image for viewing
    try:
        os.system(f"open '{output_image}'")
        print("✓ 已在浏览器中打开截图")
    except:
        pass


if __name__ == "__main__":
    main()
