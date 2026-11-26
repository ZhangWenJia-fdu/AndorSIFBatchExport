"""公开文档署名与真实 Tk 页脚布局测试。"""
import tkinter as tk
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gui

def test_attribution_and_version_in_distribution_documents():
    for name in ('README.md', '使用说明.txt'):
        text = (ROOT / name).read_text(encoding='utf-8-sig')
        assert gui.DEVELOPER in text and gui.USAGE_NOTICE in text
        assert 'v1.1.0' in text and 'v1.1.1' not in text
    assert gui.VERSION == '1.1.0'
    assert '软件由个人自主开发，仅供科研使用，不作商业用途。' in (
        ROOT / '使用说明.txt').read_text(encoding='utf-8-sig')
    for name in ('build_release.ps1', 'package_release.ps1'):
        text = (ROOT / name).read_text(encoding='utf-8-sig')
        assert 'v1.1.0' in text and 'v1.1.1' not in text

def test_footer_visible_and_log_preserved_at_window_sizes():
    root = tk.Tk()
    try:
        app = gui.ExportApp(root)
        for size in ('820x900', '780x860', '1100x950'):
            root.geometry(size)
            root.update()
            assert [w.cget('text') for w in app.footer_labels] == [gui.DEVELOPER, gui.USAGE_NOTICE]
            for label in app.footer_labels:
                assert label.winfo_ismapped()
                assert label.winfo_width() >= label.winfo_reqwidth()
                assert label.winfo_rooty() + label.winfo_height() <= root.winfo_rooty() + root.winfo_height()
            assert app.log.winfo_height() >= 35
            assert app.log.winfo_rooty() + app.log.winfo_height() < app.footer_labels[0].winfo_rooty()
            assert app.start_button.winfo_ismapped() and app.analyze_button.winfo_ismapped()
    finally:
        root.destroy()
