from pathlib import Path
import json
import pytest
from PIL import Image
from mk_studio.config import RootPolicy, compute_sha256
from mk_studio.browser.assets import BrowserAssetGenerator


def test_collect_requires_real_file_and_alpha(tmp_path):
    root=tmp_path/'scratch'; root.mkdir()
    service=BrowserAssetGenerator(RootPolicy(output_root=root), browser=None)
    img=root/'opaque.png'; Image.new('RGB',(32,32),'red').save(img)
    with pytest.raises(ValueError,match='(?i)alpha|transparen'):
        service.collect(img, role='prop', provider='chatgpt', prompt='flower')
    Image.new('RGBA',(32,32),(255,0,0,0)).save(img)
    with pytest.raises(ValueError,match='(?i)empty|visible'):
        service.collect(img, role='prop', provider='chatgpt', prompt='flower')


def test_download_collection_records_provenance(tmp_path):
    root=tmp_path/'scratch';root.mkdir()
    image=root/'flower.png'
    im=Image.new('RGBA',(16,16),(0,0,0,0));im.putpixel((8,8),(255,0,0,255));im.save(image)
    service=BrowserAssetGenerator(RootPolicy(output_root=root),browser=None)
    r=service.collect(image,role='prop',provider='chatgpt',prompt='red flower')
    assert r['status']=='downloaded_requires_review'
    assert r['sha256']==compute_sha256(image)
    assert r['provider']=='chatgpt'
