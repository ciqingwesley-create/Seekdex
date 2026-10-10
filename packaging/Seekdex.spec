from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / 'src/seekdex/resources'), 'seekdex/resources')]
binaries = []
hiddenimports = ['PySide6.QtCore','PySide6.QtGui','PySide6.QtWidgets',
    'seekdex.result_grid',
    'transformers.models.chinese_clip.configuration_chinese_clip',
    'transformers.models.chinese_clip.processing_chinese_clip',
    'transformers.models.chinese_clip.modeling_chinese_clip',
    'transformers.models.bert.tokenization_bert', 'transformers.models.clip.image_processing_clip',
    'transformers.models.clip.feature_extraction_clip', 'PIL.JpegImagePlugin','PIL.PngImagePlugin',
    'PIL.WebPImagePlugin','PIL.TiffImagePlugin','PIL.IcoImagePlugin','sqlite3',
    'huggingface_hub','safetensors.torch']
# Dynamic runtime engines have Python plugins, data files and native DLLs.
for package in ('openvino','rapidocr','onnxruntime','rawpy'):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
for package in ('seekdex','transformers','huggingface_hub','safetensors','torch','numpy','rapidocr','openvino','onnxruntime','requests','tokenizers'):
    datas += copy_metadata(package)
hiddenimports += collect_submodules('transformers.models.chinese_clip')
hiddenimports += collect_submodules('transformers.models.bert')
hiddenimports += collect_submodules('huggingface_hub')
hiddenimports += collect_submodules('PIL')
a = Analysis([str(root/'packaging/entry.py')],pathex=[str(root/'src')],binaries=binaries,
    datas=datas,hiddenimports=hiddenimports,excludes=['PyQt5','PyQt6','PySide2','tkinter','matplotlib','IPython','jupyter','pytest','cn_clip','tensorboard'],
    hooksconfig={'torch': {'include_mkl': True}},noarchive=False)
# Qt Windows wheels use the OS ICU ABI. Poppler/Conda ICU on a build-host PATH
# has a different ABI and must never shadow Windows' icuuc.dll in the bundle.
excluded_qt = {'qtvirtualkeyboardplugin.dll','Qt6VirtualKeyboard.dll','qpdf.dll','Qt6Pdf.dll',
    'Qt6Quick.dll','Qt6Qml.dll','Qt6QmlMeta.dll','Qt6QmlModels.dll','Qt6QmlWorkerScript.dll'}
a.binaries = [item for item in a.binaries if not
    (Path(item[0]).name.lower()=='icuuc.dll' or
     Path(item[0]).name.lower() in {name.lower() for name in excluded_qt} or
     ('poppler' in item[1].lower() and Path(item[0]).name.lower().startswith('icudt')))]
# Seekdex only processes still images. This optional OpenCV video driver is not
# loaded by imread/resize or RapidOCR; excluding it avoids shipping FFmpeg.
a.binaries = [item for item in a.binaries if not Path(item[0]).name.lower().startswith('opencv_videoio_ffmpeg')]
# Runtime wheels may contain previously downloaded models. Only libraries ship;
# application inference always receives explicit, verified user-cache model paths.
a.datas = [item for item in a.datas if Path(item[0]).suffix.lower() not in {'.onnx','.safetensors'}]
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='Seekdex',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    icon=str(root/'src/seekdex/resources/app.ico'),version=str(root/'build/generated/version.txt'),
    disable_windowed_traceback=True)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Seekdex')
