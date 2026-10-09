"""Consistent thumbnail cards with room reserved for filename and search hints."""
from PySide6.QtCore import Qt,QSize,QRect
from PySide6.QtGui import QColor,QPalette
from PySide6.QtWidgets import QStyledItemDelegate,QStyle


class ResultGridDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        return self.parent().gridSize()

    def paint(self,painter,option,index):
        painter.save()
        rect=option.rect.adjusted(5,5,-5,-5)
        selected=bool(option.state & QStyle.State_Selected)
        painter.fillRect(rect,option.palette.highlight() if selected else option.palette.base())
        painter.setPen(option.palette.color(QPalette.Mid))
        painter.drawRoundedRect(rect,4,4)
        painter.setPen(option.palette.color(QPalette.HighlightedText if selected else QPalette.Text))
        text=str(index.data(Qt.DisplayRole) or "").splitlines()
        line_height=option.fontMetrics.height()+3
        text_height=line_height*max(1,len(text))+8
        image_rect=rect.adjusted(7,7,-7,-text_height)
        pixmap=index.data(Qt.DecorationRole)
        if pixmap is not None and not pixmap.isNull():
            preview=pixmap.scaled(image_rect.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation)
            painter.drawPixmap(image_rect.center().x()-preview.width()//2,
                image_rect.center().y()-preview.height()//2,preview)
        else:
            painter.drawText(image_rect,Qt.AlignCenter,"等待预览" if index.model().results[index.row()].is_image else "文件")
        for number,line in enumerate(text):
            label=option.fontMetrics.elidedText(line,Qt.ElideMiddle,rect.width()-14)
            painter.drawText(QRect(rect.left()+7,rect.bottom()-text_height+4+number*line_height,
                rect.width()-14,line_height),Qt.AlignCenter,label)
        painter.restore()
