"""Managed scopes are independent from scan coverage and stable file identities."""
from pathlib import Path
from time import time
from ..paths import path_key
from ..search import SearchOptions
from .database import FileIndex


class IndexCatalog:
    def __init__(self, database: FileIndex):
        self.database = database

    def directories(self) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM indexed_directories ORDER BY root_path COLLATE NOCASE")]

    def add(self, root: Path, recursive: bool = True) -> None:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError("目录不存在或无法访问")
        self.database.connection.execute("""INSERT INTO indexed_directories(root_key,root_path,recursive,added_at)
            VALUES(?,?,?,?) ON CONFLICT(root_key) DO UPDATE SET recursive=excluded.recursive""",
            (path_key(root), str(root), int(recursive), time()))
        self.database.commit()

    def set_status(self, root: Path, kind: str, status: str, error: str = "") -> None:
        column = {"scan": "scan_status", "ai": "ai_status", "ocr": "ocr_status"}[kind]
        self.database.connection.execute(f"UPDATE indexed_directories SET {column}=?,last_error=? WHERE root_key=?",
                                         (status, error, path_key(root)))
        self.database.commit()

    def remove(self, root: Path) -> int:
        root = root.expanduser().absolute()
        connection = self.database.connection
        row = connection.execute("SELECT recursive FROM indexed_directories WHERE root_key=?",(path_key(root),)).fetchone()
        if row is None:
            return 0
        removed, params = self.database._where(SearchOptions(root, recursive=bool(row[0])))
        with self.database.transaction():
            connection.execute("DELETE FROM indexed_directories WHERE root_key=?", (path_key(root),))
            retained = self.directories()
            keep, keep_params = [], []
            for item in retained:
                where, values = self.database._where(SearchOptions(Path(item["root_path"]), recursive=bool(item["recursive"])))
                keep.append("(" + " AND ".join(where) + ")")
                keep_params.extend(values)
            sql = "DELETE FROM files WHERE " + " AND ".join(removed)
            if keep:
                sql += " AND NOT (" + " OR ".join(keep) + ")"
            count = connection.execute(sql, params+keep_params).rowcount
            connection.execute("DELETE FROM roots WHERE root_key=?", (path_key(root),))
        return count

    def statistics(self) -> list[dict]:
        from ..ai.model_cache import MODEL_ID as AI_MODEL
        from ..ocr.model_cache import MODEL_ID as OCR_MODEL
        results = []
        for item in self.directories():
            where, params = self.database._where(SearchOptions(Path(item["root_path"]), recursive=bool(item["recursive"])))
            clause = " AND ".join(where)
            total, images = self.database.connection.execute(
                "SELECT count(*),coalesce(sum(is_image),0) FROM files WHERE "+clause,params).fetchone()
            counts = {}
            for kind, table, model_column, model_id in (("ai", "image_embeddings", "model_id", AI_MODEL),
                                                        ("ocr", "ocr_results", "ocr_model_id", OCR_MODEL)):
                counts[kind] = self.database.connection.execute(f"SELECT count(*) FROM files WHERE {clause} AND is_image=1 AND EXISTS "
                    f"(SELECT 1 FROM {table} e WHERE e.file_uid=files.file_uid AND e.{model_column}=? AND e.size=files.size AND e.mtime_ns=files.mtime_ns)",params+[model_id]).fetchone()[0]
            scan = self.database.connection.execute("SELECT state,last_completed_at FROM roots WHERE root_key=?",(item["root_key"],)).fetchone()
            item.update(total=total, images=images, ai_count=counts["ai"], ocr_count=counts["ocr"],
                        coverage=self.database.coverage_state(Path(item["root_path"]),bool(item["recursive"])),
                        last_scan=scan[1] if scan else 0)
            for kind in ("ai","ocr"):
                if item[kind+"_status"] not in {"running","error"}:
                    item[kind+"_status"] = "complete" if images and counts[kind]==images else "partial" if counts[kind] else "not_indexed"
            results.append(item)
        return results
