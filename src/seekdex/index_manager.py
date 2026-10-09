"""Modeless management of selected scopes; removing an index never removes photos."""
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QPushButton, QLabel, QFileDialog, QMessageBox, QAbstractItemView)
from .product_worker import ProductTask

STATUS = {"not_indexed":"未建立","partial":"已暂停 / 部分完成","complete":"已完成","running":"正在运行","error":"有错误"}


class IndexManager(QDialog):
    roots_changed = Signal(object)
    job_running = Signal(str, bool)

    def __init__(self, database_path, runtime_parameters, parent=None):
        super().__init__(parent)
        self.database_path, self.parameters = database_path, runtime_parameters
        self.task = None
        self.items = []
        self._stopping = False
        self.setWindowTitle("索引目录管理")
        self.resize(1080, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("管理索引范围；移除目录只移除程序记录，绝不删除磁盘文件。父子目录重叠时搜索会去重。"))
        self.table = QTableWidget(0,7)
        self.table.setHorizontalHeaderLabels(["目录","基础文件","AI / 图片","OCR / 图片","最后扫描","状态","子目录"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table,1)
        row = QHBoxLayout()
        self.buttons = []
        for label, operation in (("添加目录…","add"),("刷新","scan"),("补全 AI","ai"),("补全 OCR","ocr"),
                                  ("查看错误","errors"),("从索引移除","remove")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, op=operation:self.choose(op))
            row.addWidget(button)
            self.buttons.append(button)
        self.cancel_button = QPushButton("暂停 / 取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.status = QLabel("正在读取目录状态…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.start("catalog")

    def choose(self, operation):
        if self.task is not None:
            return
        if operation == "add":
            path = QFileDialog.getExistingDirectory(self,"添加索引目录")
            if path:
                self.start("add",[Path(path)])
            return
        row = self.table.currentRow()
        if row < 0:
            self.status.setText("请先选择一个目录。")
            return
        item = self.items[row]
        if operation == "errors":
            QMessageBox.information(self,"索引错误",item["last_error"] or "没有记录的错误。文件级异常也会计入日志。")
            return
        if operation == "remove" and QMessageBox.question(self,"从索引移除",
            f"从程序索引移除 {item['root_path']}？\n不删除磁盘上的文件。\n不再被其他目录覆盖的记录及其 AI / OCR 关联将移除。",
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        self.start(operation,[Path(item["root_path"])],dict(recursive=bool(item["recursive"])))

    def start(self, operation, roots=None, parameters=None):
        if self.task is not None or self._stopping:
            return
        self.task = ProductTask(operation,self.database_path,roots=roots,
            parameters=self.parameters | (parameters or {}),parent=self)
        task = self.task
        for button in self.buttons:
            button.setEnabled(False)
        self.cancel_button.setEnabled(operation in {"scan","ai","ocr"})
        self.task.progress.connect(self.status.setText)
        self.task.completed.connect(lambda value,error:self.receive(operation,value,error))
        if operation in {"scan","ai","ocr","remove"}:
            self.job_running.emit(operation,True)
        def finished():
            self.task = None
            task.deleteLater()
            for button in self.buttons: button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            if operation in {"scan","ai","ocr","remove"}: self.job_running.emit(operation,False)
            if operation not in {"catalog","add"} and not self._stopping:
                self.start("catalog")
        task.finished.connect(finished)
        task.start()

    def receive(self, operation, value, error):
        if error:
            self.status.setText(f"操作失败：{error}")
            return
        if operation in {"catalog","add"}:
            self.items = value
            self.table.setRowCount(len(value))
            for row,item in enumerate(value):
                status = item["last_error"] and "有错误" or STATUS.get(item["coverage"],item["coverage"])
                fields = [item["root_path"],f"{item['total']:,} 已记录",f"{item['ai_count']:,} / {item['images']:,}",
                    f"{item['ocr_count']:,} / {item['images']:,}",datetime.fromtimestamp(item["last_scan"]).strftime("%Y-%m-%d %H:%M") if item["last_scan"] else "尚未扫描",
                    f"基础：{status}；AI：{STATUS.get(item['ai_status'],item['ai_status'])}；OCR：{STATUS.get(item['ocr_status'],item['ocr_status'])}","是" if item["recursive"] else "否"]
                for col,text in enumerate(fields):
                    cell = QTableWidgetItem(text)
                    cell.setToolTip(text)
                    self.table.setItem(row,col,cell)
            self.table.resizeColumnsToContents()
            self.roots_changed.emit([Path(item["root_path"]) for item in value])
            self.status.setText(f"已配置 {len(value)} 个索引目录。索引不会自动扫描未加入的盘符。")
        else:
            self.status.setText("任务结束；已完成的索引会保留。")

    def cancel(self):
        if self.task is not None:
            self.task.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在暂停，当前单图允许完成…")

    def stop(self):
        self._stopping = True
        if self.task is not None:
            self.task.cancel()
            self.task.wait()

    def closeEvent(self,event):
        # Hiding management leaves its explicit background job running.
        if self.task is not None and self.task.operation in {"scan","ai","ocr"}:
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)
