"""Generate product resources from the single metadata source and installed wheels."""
from pathlib import Path
import importlib.metadata as metadata
import json
import os
import shutil
import sys
import zipfile
import subprocess
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from seekdex import app_info


def main():
    resources = ROOT/"src"/"seekdex"/"resources"
    generated = ROOT/"build"/"generated"
    generated.mkdir(parents=True,exist_ok=True)
    resources.mkdir(parents=True,exist_ok=True)
    for name in ("LICENSE","COPYRIGHT.md","THIRD_PARTY_LICENSES.md","THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT/name,resources/name)
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtGui import QGuiApplication,QImage,QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image
    application = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(256,256,QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    QSvgRenderer(str(resources/"app.svg")).render(painter)
    painter.end()
    image.save(str(resources/"app.png"))
    with Image.open(resources/"app.png") as icon:
        icon.save(resources/"app.ico",sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    licenses = resources/"licenses"
    if licenses.exists():
        assert licenses.resolve().is_relative_to((ROOT / "src").resolve())
        shutil.rmtree(licenses)
    licenses.mkdir(exist_ok=True)
    inventory = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"] or "unknown"
        target = licenses/name
        files = []
        for record in dist.files or []:
            if any(part.lower() in {".venv","models"} for part in record.parts):continue
            filename = Path(str(record)).name.lower()
            if not (filename.startswith(("license","licence","copying","notice","copyright","third_party_notices"))
                    or any(part.lower() in {"licenses", "license", "licences"} for part in record.parts)):continue
            source = Path(dist.locate_file(record))
            if not source.is_file():continue
            target.mkdir(exist_ok=True)
            destination = target / (str(record).replace("/","_").replace("\\","_").replace(":","_"))
            shutil.copy2(source,destination)
            files.append(destination.name)
        inventory.append(dict(name=name,version=dist.version,
            license_expression=dist.metadata.get("License-Expression"),
            license=dist.metadata.get("License"), classifiers=dist.metadata.get_all("Classifier",[]),files=files))
    python_license = Path(sys.base_prefix)/"LICENSE.txt"
    if python_license.exists():shutil.copy2(python_license,licenses/"Python-LICENSE.txt")
    for source in (ROOT / "packaging/notices").glob("*.txt"):
        shutil.copy2(source, licenses / source.name)
    (licenses/"dependencies.json").write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding="utf8")
    # Tracked files include build scripts/workflows; ignore private runtime trees.
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf8").strip("\0").split("\0")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    with zipfile.ZipFile(resources/"application-source.zip","w",zipfile.ZIP_DEFLATED) as archive:
        for name in tracked:
            path = ROOT / name
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError("Source snapshot path escapes checkout")
            archive.write(path, name)
    provenance = dict(commit=head,version=app_info.VERSION,license_expression=app_info.LICENSE,
        source_url=f"{app_info.HOMEPAGE}/tree/{head}",
        source_sha256=hashlib.sha256((resources/"application-source.zip").read_bytes()).hexdigest(),
        tracked_files=len(tracked))
    (resources/"source-provenance.json").write_text(json.dumps(provenance,indent=2),encoding="utf8")
    version = app_info.VERSION
    parts = app_info.WINDOWS_VERSION
    numeric_version = ".".join(map(str, parts))
    (generated/"version.iss").write_text(f'#define ProductVersion "{version}"\n#define WindowsVersion "{numeric_version}"\n#define ProductName "{app_info.APP_NAME}"\n#define ProductPublisher "{app_info.AUTHOR}"\n#define ProductHomepage "{app_info.HOMEPAGE}"\n#define ProductLicense "{app_info.LICENSE}"\n',encoding="utf8")
    (generated/"license-notice.txt").write_text(f"Seekdex {version}: {app_info.LICENSE}\n\n"+(ROOT/"COPYRIGHT.md").read_text(encoding="utf8"),encoding="utf8")
    strings = {"CompanyName":app_info.AUTHOR,"FileDescription":app_info.DESCRIPTION,
        "FileVersion":version,"InternalName":app_info.APP_NAME,"LegalCopyright":app_info.COPYRIGHT,
        "OriginalFilename":app_info.APP_NAME+".exe","ProductName":app_info.APP_NAME,"ProductVersion":version,
        "Comments":f"Seekdex source license: {app_info.LICENSE}; third-party licenses retained."}
    text = f"VSVersionInfo(ffi=FixedFileInfo(filevers={parts!r},prodvers={parts!r},mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',["
    text += ",".join(f"StringStruct({key!r},{value!r})" for key,value in strings.items())
    text += "])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])"
    (generated/"version.txt").write_text(text,encoding="utf8")
    print(json.dumps(dict(name=app_info.APP_NAME,version=version),ensure_ascii=False))


if __name__=="__main__":main()
