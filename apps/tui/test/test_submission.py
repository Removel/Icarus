from apps.tui.src.submission import (
    DraftImage,
    PendingMessage,
    referenced_images,
)


def test_referenced_images按首次出现排序且去除未引用附件(tmp_path):
    first = DraftImage("image1", tmp_path / "first.png")
    second = DraftImage("image2", tmp_path / "second.png")
    unused = DraftImage("image3", tmp_path / "unused.png")

    assert referenced_images(
        "先 [#image2] 再 [#image1] 和 [#image2]",
        (first, second, unused),
    ) == (second, first)


def test_model_prompt保留原文并生成与图片顺序一致的映射(tmp_path):
    second = DraftImage("image2", tmp_path / "second.png")
    first = DraftImage("image1", tmp_path / "first.png")
    submission = PendingMessage(
        "比较 [#image2] 和 [#image1]",
        (second, first),
    )

    assert submission.model_prompt() == (
        "比较 [#image2] 和 [#image1]\n\n"
        "<attached_images>\n"
        "[#image2] 对应第 1 张附件图片\n"
        "[#image1] 对应第 2 张附件图片\n"
        "</attached_images>"
    )
    assert submission.image_paths == (second.path, first.path)


def test只有图片marker时补充默认模型指令(tmp_path):
    image = DraftImage("image1", tmp_path / "image.png")

    assert PendingMessage(" [#image1] ", (image,)).model_prompt().startswith(
        "请分析所附图片。\n\n<attached_images>"
    )
