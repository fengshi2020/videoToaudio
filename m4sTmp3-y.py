#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2025/6/1 09:00
# @Author  : DreamSky
# @File    : m4sTmp3-y.py
# @Software: PyCharm

import os
import re
import time
from PySide6.QtGui import QFont
from PySide6.QtCore import QThread, Signal, QObject, QProcess, Qt, QPropertyAnimation  # 添加 QPropertyAnimation 导入
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QListWidget, QProgressBar, QFileDialog,
    QMessageBox, QCheckBox, QStyle, QTextEdit
)


class FFmpegWorker(QObject):
    """
    FFmpeg 转换任务类（运行在子线程中）
    - 支持视频/音频文件转换
    - 自动处理中文路径和空格
    - 实时进度反馈
    - 音量归一化
    - 格式检测
    """
    finished_signal = Signal(str, float)  # (输出文件路径, 耗时)
    error_signal = Signal(str)
    progress_signal = Signal(float, str)  # (进度百分比, 文件名)

    def __init__(self, input_path, output_dir, use_loudnorm=False):
        super().__init__()
        self.input_path = input_path
        self.output_dir = output_dir
        self.use_loudnorm = use_loudnorm
        self._ffmpeg_path = self.find_ffmpeg()
        self._total_duration = 0.0
        self._is_cancelled = False
        self._start_time = 0
        self._has_video = self.has_video_stream()

    def find_ffmpeg(self):
        """查找 FFmpeg 可执行文件"""
        possible_paths = [
            'ffmpeg',
            '/usr/local/bin/ffmpeg',
            os.path.expanduser('~/ffmpeg'),
            os.path.join(os.getenv('HOME'), 'ffmpeg')
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
        return 'ffmpeg'  # 默认使用系统路径

    def has_video_stream(self):
        """使用 ffprobe 检查是否包含视频流"""
        cmd = [
            'ffprobe', '-v', 'error', '-show_streams',
            '-of', 'default=nw=1', self.input_path
        ]
        process = QProcess()
        process.start(cmd[0], cmd[1:])
        process.waitForFinished()
        output = process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
        return 'video' in output

    def run(self):
        """执行转换任务"""
        try:
            self._start_time = time.time()

            if not os.path.exists(self.input_path):
                self.error_signal.emit(f"❌ 输入文件不存在: {self.input_path}")
                return

            os.makedirs(self.output_dir, exist_ok=True)

            base_name = os.path.splitext(os.path.basename(self.input_path))[0]
            output_mp3 = f"{base_name}.mp3"
            output_path = os.path.join(self.output_dir, output_mp3)

            # 获取文件总时长（秒）
            duration_cmd = [self._ffmpeg_path, '-i', self.input_path]
            duration_process = QProcess()
            duration_process.start(' '.join(duration_cmd))
            duration_process.waitForFinished()
            duration_output = duration_process.readAllStandardError().data().decode('utf-8', errors='ignore')
            duration_match = re.search(r"Duration: (\d+:\d+:\d+\.\d+)", duration_output)
            if duration_match:
                self._total_duration = self._time_str_to_seconds(duration_match.group(1))

            # 检查是否需要合并分片
            audio_file = f"{os.path.splitext(self.input_path)[0]}.m4a"
            if os.path.exists(audio_file):
                merged_ts = f"{os.path.splitext(self.input_path)[0]}_merged.ts"
                self._run_merge([self.input_path, audio_file], merged_ts)
                if self._is_cancelled:
                    return
                self._run_audio_extract(merged_ts, output_path)
                if self._is_cancelled:
                    os.remove(merged_ts)
                    return
                if self._process.exitCode() == 0:
                    duration = time.time() - self._start_time
                    self.finished_signal.emit(output_path, duration)
                    os.remove(merged_ts)
                else:
                    self.error_signal.emit("❌ 音频提取失败")

            else:
                self._run_direct_convert(self.input_path, output_path)
                if self._is_cancelled:
                    return
                if self._process.exitCode() == 0:
                    duration = time.time() - self._start_time
                    self.finished_signal.emit(output_path, duration)
                else:
                    self.error_signal.emit("❌ 直接转换失败")

        except Exception as e:
            self.error_signal.emit(f"❌ 线程运行异常: {str(e)}")

    def _run_merge(self, input_files, output_file):
        """合并视频/音频分片"""
        cmd = [
            self._ffmpeg_path,
            '-y',
            '-i', input_files[0],
            '-i', input_files[1],
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-f', 'mpegts',
            output_file
        ]
        self._run_ffmpeg(cmd)

    def _run_audio_extract(self, input_file, output_file):
        """从 TS 文件中提取音频"""
        cmd = [
            self._ffmpeg_path,
            '-y',
            '-i', input_file,
            '-vn', '-ar', '44100', '-ac', '2', '-b:a', '192k'
        ]
        if self.use_loudnorm:
            cmd.extend(['-af', 'loudnorm'])
        cmd.append(output_file)
        self._run_ffmpeg(cmd)

    def _run_direct_convert(self, input_file, output_file):
        """直接转换音频文件"""
        cmd = [
            self._ffmpeg_path,
            '-y',
            '-i', input_file,
            '-vn', '-ar', '44100', '-ac', '2', '-b:a', '192k'
        ]
        if self.use_loudnorm:
            cmd.extend(['-af', 'loudnorm'])
        cmd.append(output_file)
        self._run_ffmpeg(cmd)

    def _run_ffmpeg(self, command):
        """执行 FFmpeg 命令"""
        self._process = QProcess()
        self._process.setProcessChannelMode(QProcess.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.start(command[0], command[1:])
        self._process.waitForFinished(-1)

    def _read_stdout(self):
        """读取 FFmpeg 标准输出（含进度）"""
        stdout = self._process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
        if stdout.strip():
            time_match = re.search(r"time=(\d+:\d+:\d+\.\d+)", stdout)
            if time_match and self._total_duration > 0:
                current_time = self._time_str_to_seconds(time_match.group(1))
                progress = current_time / self._total_duration
                self.progress_signal.emit(progress, os.path.basename(self.input_path))

    def _read_stderr(self):
        """读取 FFmpeg 错误输出"""
        stderr = self._process.readAllStandardError().data().decode('utf-8', errors='ignore')
        if stderr.strip():
            self.error_signal.emit(f"⚠️ FFmpeg 输出: {stderr.strip()}")

    def _time_str_to_seconds(self, time_str):
        """将时间字符串转换为秒数"""
        hms = time_str.split(':')
        if len(hms) == 3:
            hours = int(hms[0])
            minutes = int(hms[1])
            seconds = float(hms[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(hms) == 2:
            minutes = int(hms[0])
            seconds = float(hms[1])
            return minutes * 60 + seconds
        else:
            return 0.0

    def cancel(self):
        """取消任务"""
        self._is_cancelled = True
        if self._process and self._process.state() == QProcess.Running:
            self._process.kill()


# GUI 主窗口
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg 转换器 with 多功能支持")
        self.setFixedSize(600, 500)
        self.setStyleSheet("""
            QWidget {
                font-family: "Segoe UI", "Microsoft YaHei";
                font-size: 14px;
                background-color: #f0f0f0;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QLabel {
                color: #333;
            }
            QLineEdit {
                border: 1px solid #ccc;
                padding: 4px;
                border-radius: 4px;
            }
            QListWidget {
                border: 1px solid #ccc;
                padding: 4px;
                border-radius: 4px;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #ccc;
                font-size: 14px;
                padding: 4px;
            }
        """)
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        # 输入文件选择
        self.input_layout = QHBoxLayout()
        self.input_label = QLabel("输入文件:")
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("拖拽文件或点击浏览...")
        self.input_edit.setReadOnly(True)
        self.input_button = QPushButton("浏览")
        self.input_button.clicked.connect(self.select_input_files)
        self.input_layout.addWidget(self.input_label)
        self.input_layout.addWidget(self.input_edit)
        self.input_layout.addWidget(self.input_button)
        self.layout.addLayout(self.input_layout)

        # 文件列表
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.MultiSelection)  # 允许多选
        self.layout.addWidget(self.file_list)

        # 控制按钮
        self.button_layout = QVBoxLayout()  # 修改为垂直布局

        # 第一行：全选、反选、清空、删除按钮
        self.file_operation_layout = QHBoxLayout()
        self.select_all_button = QPushButton("全选")
        self.select_all_button.clicked.connect(self.select_all_files)
        self.select_none_button = QPushButton("反选")
        self.select_none_button.clicked.connect(self.select_none_files)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear_files)
        self.delete_selected_button = QPushButton("删除选中")
        self.delete_selected_button.clicked.connect(self.delete_selected_files)

        self.file_operation_layout.addWidget(self.select_all_button)
        self.file_operation_layout.addWidget(self.select_none_button)
        self.file_operation_layout.addWidget(self.clear_button)
        self.file_operation_layout.addWidget(self.delete_selected_button)

        # 第二行：选择输出目录、开始转换、取消、音量归一化按钮
        self.conversion_layout = QHBoxLayout()

        # 输出目录选择
        self.output_layout = QHBoxLayout()
        self.output_label = QLabel("输出目录:")
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择输出目录...")
        self.output_edit.setReadOnly(True)
        self.output_button = QPushButton("浏览")
        self.output_button.clicked.connect(self.select_output_dir)

        self.output_layout.addWidget(self.output_label)
        self.output_layout.addWidget(self.output_edit)
        self.output_layout.addWidget(self.output_button)

        # 开始转换、取消、音量归一化按钮
        self.start_button = QPushButton("开始转换")
        self.start_button.clicked.connect(self.start_conversion)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_conversion)
        self.loudnorm_checkbox = QCheckBox("启用音量归一化")
        self.loudnorm_checkbox.setChecked(False)

        self.conversion_layout.addLayout(self.output_layout)  # 将输出目录布局添加到第二行
        self.conversion_layout.addWidget(self.start_button)
        self.conversion_layout.addWidget(self.cancel_button)
        self.conversion_layout.addWidget(self.loudnorm_checkbox)

        # 将两行布局添加到主按钮布局中
        self.button_layout.addLayout(self.file_operation_layout)
        self.button_layout.addLayout(self.conversion_layout)

        self.layout.addLayout(self.button_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(30)  # 增加进度条高度
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 5px;
                text-align: center;
                background-color: #f0f0f0;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                          stop: 0 #4CAF50, stop: 1 #45a049);
                width: 10px;
                margin: 0.5px;
            }
""")  # 添加渐变色和动画效果

        self.layout.addWidget(self.progress_bar)

        # 日志控件（QTextEdit）
        self.status_text = QTextEdit()
        self.status_text.setFixedHeight(80)  # 固定高度
        self.status_text.setReadOnly(True)   # 设置为只读
        self.status_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # 启用垂直滚动条
        self.status_text.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #ccc;
                font-size: 14px;
                padding: 4px;
            }
        """)
        self.layout.addWidget(self.status_text)

        # 初始化路径
        self.input_paths = []
        self.output_dir = ""
        self.use_loudnorm = False

        # 设置拖拽支持
        self.setAcceptDrops(True)

        # 初始化线程管理变量
        self.active_threads = []
        self.thread_workers = {}  # 在初始化时添加此行

        # 整体进度变量
        self.total_files = 0
        self.current_files_processed = 0
        self.total_progress = 0  # 初始化总体进度变量
        self.last_progress_update_time = 0  # 新增：记录上次进度更新时间
        self.smoothing_factor = 0.8  # 新增：平滑因子，用于平滑进度更新

    def dragEnterEvent(self, event):
        """允许拖拽文件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """处理拖拽文件"""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                self.add_file_to_list(path)

    def select_input_files(self):
        """批量选择输入文件"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择输入文件",
            "",
            "支持的文件 (*.m4s *.m4a *.mp4 *.mkv *.avi);;所有文件 (*.*)"
        )
        for path in file_paths:
            self.add_file_to_list(path)

    def add_file_to_list(self, path):
        """添加文件到列表"""
        self.file_list.addItem(path)
        self.input_paths.append(path)

    def select_output_dir(self):
        """选择输出目录"""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录"
        )
        if dir_path:
            self.output_dir = dir_path
            self.output_edit.setText(dir_path)

    def start_conversion(self):
        """启动批量转换任务"""
        if not self.input_paths:
            self.status_text.append("❌ 请先选择输入文件！")
            return
        if not self.output_dir:
            self.status_text.append("❌ 请先选择输出目录！")
            return

        self.use_loudnorm = self.loudnorm_checkbox.isChecked()
        self.progress_bar.setValue(0)
        self.file_list.setEnabled(False)

        # 清理之前的线程
        for thread in self.active_threads:
            thread.quit()
            thread.wait()
        self.active_threads.clear()
        self.thread_workers.clear()  # 确保在每次启动新任务时清空线程工作对象

        self.total_files = len(self.input_paths)
        self.current_files_processed = 0
        self.total_progress = 0  # 初始化总体进度

        for input_path in self.input_paths:
            worker = FFmpegWorker(input_path, self.output_dir, use_loudnorm=self.use_loudnorm)
            thread = QThread()
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished_signal.connect(self.on_finished)
            worker.error_signal.connect(self.on_error)
            worker.progress_signal.connect(self.on_progress)
            thread.start()
            self.active_threads.append(thread)
            self.thread_workers[thread] = worker  # 将线程和工作对象关联起来

    def on_progress(self, progress, filename):
        """更新进度"""
        current_time = time.time()
        # 限制进度更新频率，避免过于频繁的更新
        if current_time - self.last_progress_update_time < 0.1:  # 至少间隔0.1秒更新一次
            return
        self.last_progress_update_time = current_time

        # 计算当前文件的总体进度贡献
        file_progress_contribution = (progress / self.total_files) * 100
        # 总体进度 = 已完成文件的进度 + 当前文件的进度贡献
        self.total_progress = (self.current_files_processed * 100 / self.total_files) + file_progress_contribution

        # 平滑进度更新
        smoothed_progress = self.smoothing_factor * self.progress_bar.value() + (1 - self.smoothing_factor) * self.total_progress
        self.progress_bar.setValue(int(smoothed_progress))  # 更新总体进度条
        self.progress_bar.setFormat(f"{int(smoothed_progress)}%")  # 更新进度条文本

        percent = int(progress * 100)
        self.status_text.append(f"🔄 正在转换 {filename}... {percent}%")
        self.status_text.verticalScrollBar().setValue(self.status_text.verticalScrollBar().maximum())  # 自动滚动到底部

    def on_finished(self, output_path, duration):
        """转换完成处理"""
        self.current_files_processed += 1
        # 计算完成后的总体进度
        self.total_progress = (self.current_files_processed / self.total_files) * 100
        self.progress_bar.setValue(int(self.total_progress))  # 更新总体进度条
        self.progress_bar.setFormat(f"{int(self.total_progress)}%")  # 更新进度条文本
        self.status_text.append(f"✅ 成功转换: {output_path}\n⏱️ 耗时: {duration:.2f} 秒")
        self.status_text.verticalScrollBar().setValue(self.status_text.verticalScrollBar().maximum())  # 自动滚动到底部

    def cancel_conversion(self):
        """取消所有任务"""
        for item in range(self.file_list.count()):
            self.file_list.item(item).setForeground(Qt.red)
        self.status_text.append("🛑 任务已取消")
        self.progress_bar.setValue(0)
        self.file_list.setEnabled(True)

        # 强制终止所有线程和 FFmpeg 进程
        for thread in self.active_threads:
            if thread in self.thread_workers:
                worker = self.thread_workers[thread]
                if hasattr(worker, '_process') and worker._process and worker._process.state() == QProcess.Running:
                    worker._process.kill()
                worker.cancel()  # 调用工作对象的取消方法
            thread.quit()
            thread.wait()
        self.active_threads.clear()
        self.thread_workers.clear()

    def on_error(self, message):
        """转换错误处理"""
        self.status_text.append(f"❌ {message}")
        self.progress_bar.setValue(0)
        self.file_list.setEnabled(True)
        self.status_text.verticalScrollBar().setValue(self.status_text.verticalScrollBar().maximum())

    def closeEvent(self, event):
        """窗口关闭时强制终止所有线程和 FFmpeg 进程"""
        self.cancel_conversion()
        event.accept()

    def select_all_files(self):
        """全选文件"""
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            item.setSelected(True)

    def select_none_files(self):
        """反选文件"""
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            item.setSelected(not item.isSelected())

    def clear_files(self):
        """清空文件列表"""
        self.file_list.clear()
        self.input_paths = []

    def delete_selected_files(self):
        """删除选中的文件"""
        selected_items = self.file_list.selectedItems()
        for item in selected_items:
            self.file_list.takeItem(self.file_list.row(item))
            self.input_paths.remove(item.text())


# 注意：为了将此脚本打包成单个可执行文件，可以使用 PyInstaller 工具。
# 安装 PyInstaller:
#   pip install pyinstaller
# 打包命令:
#   pyinstaller --onefile --windowed --icon=icon.ico m4sTmp3-y.py
# 其中:
#   --onefile: 生成单个可执行文件。
#   --windowed: 不显示控制台窗口（适用于GUI应用）。
#   --icon=icon.ico: 指定图标文件（可选）。

# 主程序入口
if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    app.setFont(QFont("Arial"))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())