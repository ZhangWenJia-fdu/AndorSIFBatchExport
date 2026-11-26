"""v1 四格式、三模式及审计契约。"""
from pathlib import Path
import csv, itertools, sys
import numpy as np
from PIL import Image
import pytest, tifffile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import batch_export_sif as b
import gui

@pytest.mark.parametrize('selection',[s for s in itertools.product((False,True),repeat=4) if any(s)])
@pytest.mark.parametrize('mode',['custom','minmax','0-65535'])
def test_format_combinations(tmp_path,monkeypatch,selection,mode):
    t8,t16,raw,png=selection
    source=tmp_path/'in';source.mkdir();p=source/'image.sif';p.write_bytes(b'unchanged')
    frame=np.array([[100.25,200.5],[70000,300.75]],dtype=np.float32)
    monkeypatch.setattr(b.sif_parser,'np_open',lambda _:(frame[None],{}))
    options=gui.build_options(str(source),str(tmp_path/'out'),'100','400',t8,t16,False,raw,png,mode)
    assert b.run_batch(options)[:3]==(1,1,0)
    label=b.range_label(mode,100,400);out=tmp_path/'out'
    paths=[out/'display_tiff_8bit'/f'image_8bit_{label}.tif',out/'display_tiff_16bit'/f'image_16bit_{label}.tif',out/'raw_tiff'/'image_raw.tif',out/'display_png'/f'image_8bit_{label}.png']
    assert [p.exists() for p in paths]==list(selection)
    if raw:assert np.array_equal(tifffile.imread(paths[2]),frame)
    if png:
        with Image.open(paths[3]) as image: pixels=np.asarray(image)
        assert np.array_equal(pixels,np.flipud(b.map_display(frame,100,400,8,mode)))
        if t8:assert np.array_equal(pixels,tifffile.imread(paths[0]))
    report=next((out/'reports').glob('*_formats.csv'))
    with report.open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
    assert len(rows)==sum(selection)
    for row in rows:
        assert row['verification']=='PASS' and row['status']=='PASS_INTERNAL'
        if row['format']=='raw':assert row['data_range']=='none' and row['actual_min']=='' and row['orientation']=='native'
        else:
            lo,hi=b.mapping_range(frame,mode,100,400)
            assert float(row['actual_min'])==lo and float(row['actual_max'])==hi
    assert p.read_bytes()==b'unchanged'

@pytest.mark.parametrize('mode',['','custom','minmax','0-65535'])
def test_raw_only_ignores_range(tmp_path,monkeypatch,mode):
    (tmp_path/'x.sif').touch();frame=np.array([[1.25,-3],[1e5,2]],dtype=np.float32)
    monkeypatch.setattr(b.sif_parser,'np_open',lambda _:(frame,{}))
    opts=gui.build_options(str(tmp_path),str(tmp_path/'out'),'bad','',False,False,False,True,False,mode)
    assert b.run_batch(opts)[:3]==(1,1,0)
    assert np.array_equal(tifffile.imread(tmp_path/'out/raw_tiff/x_raw.tif'),frame)

@pytest.mark.parametrize('low,high,label',[(12694,28977,'custom_12694-28977'),(-1.5,3.25,'custom_m1p5-3p25'),(0.0000001,0.0000002,'custom_1em07-2em07')])
def test_range_encoding(low,high,label):
    assert b.range_label('custom',low,high)==label
    assert not set('<>:"/\\|?*') & set(label)

def test_custom_algorithm_unchanged():
    x=np.array([[-2.5,1,1.001],[4.5,1e5,0]],dtype=np.float32)
    for bits in (8,16):assert np.array_equal(b.map_display(x,-1.5,4.5,bits),b.scale_to_display_dtype(x,-1.5,4.5,bits))

def test_minmax_constant_is_explicitly_unverified():
    with pytest.raises(ValueError,match='尚未验证'):b.map_display(np.ones((2,2)),0,1,16,'minmax')

def test_missing_range_for_display(tmp_path):
    with pytest.raises(ValueError,match='Data Range'):gui.build_options(str(tmp_path),str(tmp_path/'out'),'','',False,False,False,False,True,'')

def test_distinct_ranges_and_no_overwrite(tmp_path,monkeypatch):
    (tmp_path/'x.sif').touch();frame=np.array([[1,2],[3,4]],dtype=np.float32)
    monkeypatch.setattr(b.sif_parser,'np_open',lambda _:(frame,{}))
    for hi in (4,5):
        options=gui.build_options(str(tmp_path),str(tmp_path/'out'),'0',str(hi),True,False,False)
        assert b.run_batch(options)[:3]==(1,1,0)
    files=list((tmp_path/'out/display_tiff_8bit').glob('*.tif'));before={p:p.read_bytes() for p in files}
    assert len(files)==2 and b.run_batch(options)[:3]==(1,0,1)
    assert all(p.read_bytes()==v for p,v in before.items())

def test_multiframe_relative_paths(tmp_path,monkeypatch):
    source=tmp_path/'in';(source/'child').mkdir(parents=True)
    (source/'x.sif').touch();(source/'child/x.sif').touch()
    monkeypatch.setattr(b.sif_parser,'np_open',lambda _:(np.arange(8,dtype=np.float32).reshape(2,2,2),{}))
    options=gui.build_options(str(source),str(tmp_path/'out'),'0','7',True,False,True)
    assert b.run_batch(options)[:3]==(2,2,0)
    files=list((tmp_path/'out/display_tiff_8bit').rglob('*.tif'));assert len(files)==4
    assert (tmp_path/'out/display_tiff_8bit/child/x_frame0002_8bit_custom_0-7.tif').exists()

def test_gui_dynamic_range_and_duplicate_start(monkeypatch):
    import tkinter as tk
    try: root=tk.Tk()
    except tk.TclError as exc: pytest.skip(str(exc))
    root.withdraw();app=gui.ExportApp(root)
    try:
        for mode,state in [('custom','normal'),('minmax','disabled'),('0-65535','disabled'),('','disabled')]:
            app.range_var.set(mode);app._range_changed()
            assert str(app.min_entry.cget('state'))==state and str(app.max_entry.cget('state'))==state
        app.active=True
        monkeypatch.setattr(gui,'build_options',lambda *a,**kw:pytest.fail('Repeated start attempted'))
        app.start_export();assert app.worker is None
    finally:root.destroy()
