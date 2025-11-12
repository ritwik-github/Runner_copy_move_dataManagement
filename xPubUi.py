# // XPUB UI PUBLISHER & ARCHIVER
# Author:: Ritwik-G (ritwik.g@zebufx.com)

import sys
import os
import datetime
import subprocess
import re
import json
import stat 
import psutil
import time
from PySide6 import QtWidgets, QtCore, QtGui

# ... (ProgressDialog, RobocopyWorker, and InfoDialog classes are unchanged) ...
class ProgressDialog(QtWidgets.QDialog):
    abort_clicked = QtCore.Signal()
    pause_toggled = QtCore.Signal(bool)
    def __init__(self, parent=None):
        super(ProgressDialog, self).__init__(parent)
        self.setWindowTitle("Operation in Progress...")
        self.setMinimumSize(600, 400)
        
        self.log_viewer = QtWidgets.QTextEdit(); self.log_viewer.setReadOnly(True)
        self.progress_bar = QtWidgets.QProgressBar(); self.progress_bar.setTextVisible(False)
        self.speed_label = QtWidgets.QLabel("Speed: N/A")
        self.pause_button = QtWidgets.QPushButton("Pause"); self.pause_button.setCheckable(True)
        self.abort_button = QtWidgets.QPushButton("Abort")
        self.close_button = QtWidgets.QPushButton("Close"); self.close_button.setEnabled(False)
        
        progress_layout = QtWidgets.QHBoxLayout(); progress_layout.addWidget(self.progress_bar); progress_layout.addWidget(self.speed_label)
        button_layout = QtWidgets.QHBoxLayout(); button_layout.addStretch(); button_layout.addWidget(self.pause_button); button_layout.addWidget(self.abort_button); button_layout.addWidget(self.close_button)
        main_layout = QtWidgets.QVBoxLayout(self); main_layout.addWidget(QtWidgets.QLabel("Log:")); main_layout.addWidget(self.log_viewer); main_layout.addLayout(progress_layout); main_layout.addLayout(button_layout)
        
        self.abort_button.clicked.connect(self.abort_clicked.emit); self.pause_button.toggled.connect(self.on_pause_toggled); self.close_button.clicked.connect(self.accept)
    
    def on_pause_toggled(self, checked):
        self.pause_button.setText("Resume" if checked else "Pause"); self.pause_toggled.emit(checked)
    
    @QtCore.Slot(str)
    def add_log(self, message): self.log_viewer.append(message)
    
    @QtCore.Slot(int)
    def set_progress(self, value): self.progress_bar.setValue(value)
    
    @QtCore.Slot(str)
    def set_speed(self, speed_text): self.speed_label.setText(f"Speed: {speed_text}")

    def on_finished(self, success, success_message="OPERATION COMPLETED SUCCESSFULLY", failure_message="OPERATION FAILED OR ABORTED"):
        self.pause_button.setEnabled(False); self.abort_button.setEnabled(False); self.close_button.setEnabled(True)
        if success:
            self.add_log(f"\n--- {success_message} ---"); self.progress_bar.setValue(100)
        else:
            self.add_log(f"\n--- {failure_message} ---")
        self.setWindowTitle("Operation Finished")

class RobocopyWorker(QtCore.QObject):
    progress_updated = QtCore.Signal(int); log_message = QtCore.Signal(str); finished = QtCore.Signal(bool)
    speed_updated = QtCore.Signal(str)

    def __init__(self, copy_jobs, is_move=False, throttle="Fast", config_data={}):
        super().__init__()
        self.copy_jobs = copy_jobs; self.is_move = is_move; self.throttle = throttle
        self.config_data = config_data # Store config data
        self._is_aborted = False; self.process = None; self._psutil_process = None
    
    def run(self):
        total_jobs = len(self.copy_jobs); success = True
        for i, (source, dest) in enumerate(self.copy_jobs):
            if self._is_aborted: success = False; break
            operation = "Moving" if self.is_move else "Copying"; os.makedirs(os.path.dirname(dest), exist_ok=True)
            self.log_message.emit(f"{operation} '{os.path.basename(source)}'..."); self.log_message.emit(f"  Source: {source}\n  Destination: {dest}")
            
            command = ["robocopy", source, dest, "/E", "/R:2", "/W:5", "/NJH", "/NJS", "/ETA"]
            if self.is_move: 
                command.append("/MOV")

            # Use the config value if "Slow" is selected
            if self.throttle == "Slow":
                delay = self.config_data.get("throttle_delay_ms", 100) # Read from config, fallback to 100
                command.append(f"/IPG:{delay}")
            else: # Fast mode
                command.append("/MT")
            
            self.process = QtCore.QProcess(); self.process.readyReadStandardOutput.connect(lambda: self._read_stdout(i, total_jobs))
            loop = QtCore.QEventLoop(); self.process.finished.connect(loop.quit); self.process.start("robocopy", command[1:])
            
            if self.process.waitForStarted(): self._psutil_process = psutil.Process(self.process.processId()); loop.exec()
            else: self.log_message.emit(f"ERROR: Could not start robocopy for {source}"); success = False; break
            
            if self._is_aborted: success = False; break
            if self.process.exitCode() >= 8: self.log_message.emit(f"ERROR: Robocopy failed with exit code {self.process.exitCode()}"); success = False; break
        
        self.finished.emit(success)

    def _read_stdout(self, job_index, total_jobs):
        if not self.process: return
        output = self.process.readAllStandardOutput().data().decode().strip()
        if not output: return
        for line in output.split('\r'):
            line = line.strip(); 
            if not line: continue
            self.log_message.emit(line)
            match = re.search(r"(\d+\.?\d*)\s*%", line)
            if match:
                percentage = float(match.group(1)); overall_progress = int(((job_index + (percentage / 100.0)) / total_jobs) * 100); self.progress_updated.emit(overall_progress)
            
            # FIX: More robust regex to capture any speed unit
            speed_match = re.search(r"Speed:\s+([\d,.]+\s+[KMG]?B/sec)", line)
            if speed_match:
                self.speed_updated.emit(speed_match.group(1).strip())

    def abort(self):
        self.log_message.emit("--- ABORTING ---"); self._is_aborted = True
        if self.process and self.process.state() == QtCore.QProcess.Running: self.process.kill()
    
    @QtCore.Slot(bool)
    def toggle_pause(self, paused):
        if not self._psutil_process or not self._psutil_process.is_running(): return
        try:
            if paused and self._psutil_process.status() == psutil.STATUS_RUNNING: self._psutil_process.suspend(); self.log_message.emit("--- PROCESS PAUSED ---")
            elif not paused and self._psutil_process.status() == psutil.STATUS_STOPPED: self._psutil_process.resume(); self.log_message.emit("--- PROCESS RESUMED ---")
        except psutil.NoSuchProcess: pass
        except Exception as e: self.log_message.emit(f"Pause/Resume Error: {e}")


# /////////////////////////////////////////////
# REVISED - Archive Worker Thread
# \\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
class ArchiveWorker(QtCore.QObject):
    progress_updated = QtCore.Signal(int)
    log_message = QtCore.Signal(str)
    finished = QtCore.Signal(bool)
    archive_summary_ready = QtCore.Signal(list)

    def __init__(self, shot_paths, threshold, max_age_days, max_age_enabled, config_data):
        super().__init__()
        self.shot_paths = shot_paths
        self.threshold = threshold
        self.max_age_days = max_age_days
        self.max_age_enabled = max_age_enabled
        self.config_data = config_data
        self._is_aborted = False

    def run(self):
        try:
            dept = self.config_data.get("active_department")
            source_template = self.config_data.get("departments", {}).get(dept, {}).get("source_path")
            if not source_template:
                self.log_message.emit("ERROR: No 'source_path' in config for active department.")
                self.finished.emit(False); return

            folders_to_clean = []
            cleaned_paths_log = []
            
            # Loop through all selected shots
            for shot_path in self.shot_paths:
                if self._is_aborted: break
                user_base_path = os.path.join(shot_path, source_template.replace('/', os.sep))
                if not os.path.exists(user_base_path): continue

                user_dirs = [d for d in os.listdir(user_base_path) if os.path.isdir(os.path.join(user_base_path, d))]
                for user in user_dirs:
                    preview_path = os.path.join(user_base_path, user, "renders", "preview")
                    if not os.path.exists(preview_path): continue
                    render_names = [r for r in os.listdir(preview_path) if os.path.isdir(os.path.join(preview_path, r))]
                    for render in render_names:
                        render_path = os.path.join(preview_path, render)
                        versions_with_mtime = []
                        for v in os.listdir(render_path):
                            v_path = os.path.join(render_path, v)
                            if os.path.isdir(v_path):
                                versions_with_mtime.append((v, os.path.getmtime(v_path)))
                        
                        versions_with_mtime.sort(key=lambda x: x[1])
                        sorted_versions = [v[0] for v in versions_with_mtime]
                        
                        versions_to_delete = set()

                        if len(sorted_versions) > self.threshold:
                            versions_to_delete.update(sorted_versions[:-self.threshold])

                        if self.max_age_enabled:
                            for version in sorted_versions:
                                full_path = os.path.join(render_path, version)
                                age = self._get_folder_age_in_days(full_path)
                                if age > self.max_age_days:
                                    versions_to_delete.add(version)

                        for version in versions_to_delete:
                            folders_to_clean.append(os.path.join(render_path, version))
                            cleaned_paths_log.append(f"{os.path.basename(shot_path)}/{render}/{version} ({user})")

            if self._is_aborted: self.finished.emit(False); return
            if not folders_to_clean:
                self.log_message.emit("All versions are within the specified filters. Nothing to archive."); self.finished.emit(True); return

            total_to_clean = len(folders_to_clean)
            for i, folder_path in enumerate(folders_to_clean):
                if self._is_aborted: self.finished.emit(False); return
                
                self.log_message.emit(f"Cleaning: .../{'/'.join(folder_path.split(os.sep)[-5:])}")
                for root, dirs, files in os.walk(folder_path, topdown=False):
                    for name in files:
                        try: os.remove(os.path.join(root, name))
                        except OSError as e: self.log_message.emit(f"  ERROR deleting file {name}: {e}")
                
                progress = int(((i + 1) / total_to_clean) * 100)
                self.progress_updated.emit(progress)
            
            self.log_message.emit("\nArchive operation complete.")
            self.archive_summary_ready.emit(cleaned_paths_log)
            self.finished.emit(True)

        except Exception as e:
            self.log_message.emit(f"FATAL ERROR during archive: {e}"); self.finished.emit(False)
    
    def abort(self):
        self.log_message.emit("--- ABORTING ---"); self._is_aborted = True

    def _get_directory_size(self, path):
        # ... (unchanged)
        total_size = 0; 
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp): total_size += os.path.getsize(fp)
        except FileNotFoundError: return 0
        return total_size

    def _get_folder_age_in_days(self, path):
        # ... (unchanged)
        try:
            mtime = os.path.getmtime(path)
            age_seconds = time.time() - mtime
            return age_seconds / (24 * 3600)
        except FileNotFoundError: return 0

class InfoDialog(QtWidgets.QDialog):
    def __init__(self, title, content, parent=None):
        super(InfoDialog, self).__init__(parent)
        self.setWindowTitle(title); self.setMinimumSize(500, 400)
        text_viewer = QtWidgets.QTextEdit(); text_viewer.setReadOnly(True); text_viewer.setMarkdown(content)
        close_button = QtWidgets.QPushButton("Close"); close_button.clicked.connect(self.accept)
        layout = QtWidgets.QVBoxLayout(self); layout.addWidget(text_viewer); layout.addWidget(close_button, 0, QtCore.Qt.AlignRight)


# /////////////////////////////////////////////
# REVISED - Shot Scanner Worker
# \\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
class ShotScannerWorker(QtCore.QObject):
    """Scans all shots in a sequence to get their total size based on data source."""
    shot_found = QtCore.Signal(str, float) # FIX: Changed from int to float
    finished = QtCore.Signal()

    def __init__(self, seq_path, config_data, source_mode):
        super().__init__()
        self.seq_path = seq_path
        self.config_data = config_data
        self.source_mode = source_mode

    def run(self):
        try:
            dept = self.config_data.get("active_department")
            dept_paths = self.config_data.get("departments", {}).get(dept, {})
            
            shots = [s for s in os.listdir(self.seq_path) if os.path.isdir(os.path.join(self.seq_path, s))]
            for shot in sorted(shots):
                total_size = 0.0 # Use float
                
                if self.source_mode == "WIP":
                    source_template = dept_paths.get("source_path")
                    if source_template:
                        user_base_path = os.path.join(self.seq_path, shot, source_template.replace('/', os.sep))
                        total_size = self._get_directory_size(user_base_path)
                
                elif self.source_mode == "FINAL":
                    publish_template = dept_paths.get("publish_path")
                    if publish_template:
                        publish_base_path = os.path.join(self.seq_path, shot, publish_template.replace('/', os.sep))
                        total_size = self._get_directory_size(publish_base_path)

                self.shot_found.emit(shot, total_size)
        except Exception as e:
            print(f"Shot Scanner Error: {e}")
        finally:
            self.finished.emit()

    def _get_directory_size(self, path):
        total_size = 0.0 # FIX: Use float to prevent overflow
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp): 
                        total_size += os.path.getsize(fp)
        except FileNotFoundError: 
            return 0.0
        return total_size

# /////////////////////////////////////////////
# NEW - Status Icon Summary Widget
# \\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
class StatusIconSummary(QtWidgets.QWidget):
    """A widget to display three status icons."""
    def __init__(self, parent=None):
        super(StatusIconSummary, self).__init__(parent)
        self.red_icon = QtWidgets.QLabel()
        self.yellow_icon = QtWidgets.QLabel()
        self.green_icon = QtWidgets.QLabel()

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.red_icon)
        layout.addWidget(self.yellow_icon)
        layout.addWidget(self.green_icon)
        layout.addStretch()

    def update_summary(self, counts, icons):
        """Updates the icons based on analysis."""
        if not counts or sum(counts.values()) == 0:
            dominant_color = None
        else:
            dominant_color = max(counts, key=counts.get)

        self.red_icon.setPixmap(icons['red'] if dominant_color == 'red' else icons['red_dim'])
        self.yellow_icon.setPixmap(icons['yellow'] if dominant_color == 'yellow' else icons['yellow_dim'])
        self.green_icon.setPixmap(icons['green'] if dominant_color == 'green' else icons['green_dim'])
    
    def reset(self, icons):
        """Resets to the default grey state."""
        self.red_icon.setPixmap(icons['grey'])
        self.yellow_icon.setPixmap(icons['grey'])
        self.green_icon.setPixmap(icons['grey'])

# /////////////////////////////////////////////
# UI main Code Class
# \\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
class mainWindow(QtWidgets.QWidget):
    def __init__(self):
        super(mainWindow, self).__init__()
        self.setObjectName("mainWidget"); self.setWindowTitle("xPubUi      ( v2.2.0 )"); self.setGeometry(500, 200, 900, 700); self.setWindowIcon(self._create_icon_from_char("✈️"))
        self.baseLayout = QtWidgets.QVBoxLayout(self); self.baseLayout.setContentsMargins(5,5,5,5); self.baseLayout.setSpacing(10)
        
        self.config_data = {}; self.show_root_path = ""
        self.icon_age_threshold = 30 

        self.menuBar = QtWidgets.QMenuBar(self)
        self.mainMenu = self.menuBar.addMenu("Menu")
        self.configMenu = self.mainMenu.addMenu("Config")
        self.helpMenu = self.mainMenu.addMenu("Help")
        self.load_config_action = self.configMenu.addAction("Load Config File...")
        self.how_to_action = self.helpMenu.addAction("How To Operate")
        self.release_notes_action = self.helpMenu.addAction("Release Notes")
        self.baseLayout.setMenuBar(self.menuBar)

        self._create_icons()
        
        self.tabWidget = QtWidgets.QTabWidget()
        self.publisherTab = QtWidgets.QWidget()
        self.publisherTabLayout = QtWidgets.QVBoxLayout(self.publisherTab); self.publisherTabLayout.setContentsMargins(5, 5, 5, 5)
        self.tabWidget.addTab(self.publisherTab, "Publisher")
        self.archiverTab = QtWidgets.QWidget()
        self.archiverTabLayout = QtWidgets.QVBoxLayout(self.archiverTab); self.archiverTabLayout.setContentsMargins(5, 5, 5, 5)
        self.tabWidget.addTab(self.archiverTab, "Archiver")
        
        self.shot_logs = []; self.current_shot_log_index = -1
        self.archive_logs = []; self.current_archive_log_index = -1
        
        # --- Publisher Widgets ---
        self.authorGBox = QtWidgets.QGroupBox(""); self.authorGBox.setMaximumHeight(80); self.authorGBoxLayot = QtWidgets.QHBoxLayout(self.authorGBox); self.authorGBoxLayot.setContentsMargins(5, 5, 5, 5)
        self.artistLbl = QtWidgets.QLabel("Artist"); self.artistLineEdit = QtWidgets.QLineEdit(); self.deptLbl = QtWidgets.QLabel("Host"); self.deptLineEdit = QtWidgets.QLineEdit(); self.dateLbl = QtWidgets.QLabel("Date"); self.dateLineEdit = QtWidgets.QLineEdit()
        self.artistLineEdit.setReadOnly(True); self.deptLineEdit.setReadOnly(True); self.dateLineEdit.setReadOnly(True)
        self.jobLbl = QtWidgets.QLabel("Show"); self.jobComBox = QtWidgets.QComboBox()
        self.seqNameLbl = QtWidgets.QLabel("Sequence"); self.seqNameComBox = QtWidgets.QComboBox()
        self.shotNameLbl = QtWidgets.QLabel("Shot"); self.shotNameComBox = QtWidgets.QComboBox()
        self.throttlePubLbl = QtWidgets.QLabel("Throttle IPG | MT")
        self.throttlePubComboBox = QtWidgets.QComboBox(); self.throttlePubComboBox.addItems(["Slow", "Fast"])
        self.rendersTree = QtWidgets.QTreeWidget()
        self.rendersTree.setHeaderLabels(["Render / Version", "Date Modified", "Frame Status"]) # New headers
        self.rendersTree.setAlternatingRowColors(True)
        self.rendersTree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.rendersTree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.rendersTree.setColumnWidth(1, 120) # Width for Date column
        self.rendersTree.setColumnWidth(2, 40)  # Width for Status icons
        self.projectGBox = QtWidgets.QGroupBox(""); self.projectGBoxLayout = QtWidgets.QVBoxLayout(self.projectGBox)
        self.commentGBox = QtWidgets.QGroupBox("Comment"); self.commentGBoxLayout = QtWidgets.QVBoxLayout(self.commentGBox)
        self.commentTextEdit = QtWidgets.QTextEdit(); self.commentTextEdit.setPlaceholderText("Select a project to begin..."); self.commentTextEdit.setToolTip("Add Comment for record/Log while releasing"); self.commentTextEdit.setMinimumHeight(100)
        self.move_radio_btn = QtWidgets.QRadioButton("⚠️ Clear Source"); self.move_radio_btn.setToolTip("Check this to MOVE files and clear the source directory after publishing."); self.move_radio_btn.setEnabled(False)
        self.daily_check_box = QtWidgets.QCheckBox("Make Daily"); self.daily_check_box.setToolTip("Future implementation for creating dailies."); self.daily_check_box.setEnabled(False)
        self.publishBtn = QtWidgets.QPushButton("Publish"); self.publishBtn.setEnabled(False); self.publishBtn.setToolTip("Select a version and add a comment to enable.")
        self.cancelBtn = QtWidgets.QPushButton("Cancel"); self.cancelBtn.setToolTip("to Close/Cancel UI")
        self.prevLogBtn = QtWidgets.QPushButton("▲"); self.nextLogBtn = QtWidgets.QPushButton("▼"); self.prevLogBtn.setFixedWidth(30); self.nextLogBtn.setFixedWidth(30); self.prevLogBtn.setFixedHeight(30); self.nextLogBtn.setFixedHeight(30)
        self.prevLogBtn.setToolTip("View previous log entry"); self.nextLogBtn.setToolTip("View next log entry"); self.prevLogBtn.setEnabled(False); self.nextLogBtn.setEnabled(False)
        
        # --- Archiver Widgets ---
        self.archiveProjectGBox = QtWidgets.QGroupBox(""); self.archiveProjectGBoxLayout = QtWidgets.QHBoxLayout(self.archiveProjectGBox)
        self.archiveShowLbl = QtWidgets.QLabel("Show"); self.archiveShowComBox = QtWidgets.QComboBox()
        self.archiveSeqLbl = QtWidgets.QLabel("Sequence"); self.archiveSeqComBox = QtWidgets.QComboBox()
        self.archiveDataSourceLbl = QtWidgets.QLabel("Data Source"); self.archiveDataSourceComBox = QtWidgets.QComboBox(); self.archiveDataSourceComBox.addItems(["WIP", "FINAL"])
        self.statusSummary = StatusIconSummary(); self.statusSummary.reset(self.summary_icons)
        self.archiveTree = QtWidgets.QTreeWidget(); self.archiveTree.setHeaderLabels(["Shot / Render / Version", "Size", "Weight"]); self.archiveTree.setAlternatingRowColors(True); self.archiveTree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch); self.archiveTree.setColumnWidth(1, 80); self.archiveTree.setColumnWidth(2, 40); self.archiveTree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.archiveFilterGBox = QtWidgets.QGroupBox("Filters"); self.archiveFilterGBoxLayout = QtWidgets.QHBoxLayout(self.archiveFilterGBox)
        self.thresholdLbl = QtWidgets.QLabel("Threshold:"); self.thresholdSpinBox = QtWidgets.QSpinBox(); self.thresholdSpinBox.setRange(0, 50); self.thresholdSpinBox.setValue(5)
        self.maxAgeRadioButton = QtWidgets.QRadioButton("Max Age"); self.maxAgeLineEdit = QtWidgets.QLineEdit("30"); self.maxAgeLineEdit.setValidator(QtGui.QIntValidator(1, 999)); self.maxAgeLineEdit.setFixedWidth(40); self.maxAgeLineEdit.setEnabled(False)
        self.maxAgeDaysLbl = QtWidgets.QLabel("Days")
        self.throttleLbl = QtWidgets.QLabel("Throttle:"); self.throttleComboBox = QtWidgets.QComboBox(); self.throttleComboBox.addItems(["Fast", "Slow"])
        self.archiveCommentGBox = QtWidgets.QGroupBox("Comment"); self.archiveCommentGBoxLayout = QtWidgets.QVBoxLayout(self.archiveCommentGBox)
        self.archiveCommentTextEdit = QtWidgets.QTextEdit(); self.archiveCommentTextEdit.setPlaceholderText("Add comments for the archive operation..."); self.archiveCommentTextEdit.setMinimumHeight(100)
        self.prevArchiveLogBtn = QtWidgets.QPushButton("▲"); self.prevArchiveLogBtn.setFixedSize(30,30); self.prevArchiveLogBtn.setEnabled(False)
        self.nextArchiveLogBtn = QtWidgets.QPushButton("▼"); self.nextArchiveLogBtn.setFixedSize(30,30); self.nextArchiveLogBtn.setEnabled(False)
        self.archiveBtn = QtWidgets.QPushButton("Archive"); self.archiveBtn.setEnabled(False)
        self.cancelArchiveBtn = QtWidgets.QPushButton("Cancel")

        # --- Assemble Layouts ---
        self.baseLayout.addWidget(self.authorGBox); self.authorGBoxLayot.addWidget(self.artistLbl); self.authorGBoxLayot.addWidget(self.artistLineEdit); self.authorGBoxLayot.addWidget(self.deptLbl); self.authorGBoxLayot.addWidget(self.deptLineEdit); self.authorGBoxLayot.addWidget(self.dateLbl); self.authorGBoxLayot.addWidget(self.dateLineEdit)
        
        self.inputFilterLayout = QtWidgets.QHBoxLayout()
        for label, widget in [(self.jobLbl, self.jobComBox), (self.seqNameLbl, self.seqNameComBox), (self.shotNameLbl, self.shotNameComBox), (self.throttlePubLbl, self.throttlePubComboBox)]:
            col = QtWidgets.QVBoxLayout(); col.addWidget(label); col.addWidget(widget); self.inputFilterLayout.addLayout(col)
        self.projectGBoxLayout.addLayout(self.inputFilterLayout)
        self.shotBrowserLayout = QtWidgets.QHBoxLayout(); self.shotBrowserLayout.addWidget(self.move_radio_btn); self.shotBrowserLayout.addStretch(); self.shotBrowserLayout.addWidget(self.prevLogBtn); self.shotBrowserLayout.addWidget(self.nextLogBtn)
        self.commentGBoxLayout.addLayout(self.shotBrowserLayout); self.commentGBoxLayout.addWidget(self.commentTextEdit)
        
        pub_legend_layout = self._create_publisher_legend()
        self.shotBtnLayout = QtWidgets.QHBoxLayout()
        self.shotBtnLayout.addLayout(pub_legend_layout)
        self.shotBtnLayout.addStretch()
        self.shotBtnLayout.addWidget(self.daily_check_box); self.shotBtnLayout.addWidget(self.publishBtn); self.shotBtnLayout.addWidget(self.cancelBtn)
        
        self.publisherTabLayout.addWidget(self.projectGBox); self.publisherTabLayout.addWidget(self.rendersTree); self.publisherTabLayout.addWidget(self.commentGBox); self.publisherTabLayout.addLayout(self.shotBtnLayout)
        
        archive_show_col = QtWidgets.QVBoxLayout(); archive_show_col.addWidget(self.archiveShowLbl); archive_show_col.addWidget(self.archiveShowComBox)
        archive_seq_col = QtWidgets.QVBoxLayout(); archive_seq_col.addWidget(self.archiveSeqLbl)
        archive_seq_hbox = QtWidgets.QHBoxLayout(); archive_seq_hbox.addWidget(self.archiveSeqComBox); archive_seq_hbox.addWidget(self.statusSummary); archive_seq_col.addLayout(archive_seq_hbox)
        archive_ds_col = QtWidgets.QVBoxLayout(); archive_ds_col.addWidget(self.archiveDataSourceLbl); archive_ds_col.addWidget(self.archiveDataSourceComBox)
        self.archiveProjectGBoxLayout.addLayout(archive_show_col); self.archiveProjectGBoxLayout.addLayout(archive_seq_col); self.archiveProjectGBoxLayout.addLayout(archive_ds_col); self.archiveProjectGBoxLayout.addStretch()
        
        self.archiveFilterGBoxLayout.addWidget(self.thresholdLbl); self.archiveFilterGBoxLayout.addWidget(self.thresholdSpinBox); self.archiveFilterGBoxLayout.addWidget(self.maxAgeRadioButton); self.archiveFilterGBoxLayout.addWidget(self.maxAgeLineEdit); self.archiveFilterGBoxLayout.addWidget(self.maxAgeDaysLbl)
        self.archiveFilterGBoxLayout.addStretch()
        self.archiveFilterGBoxLayout.addWidget(self.throttleLbl); self.archiveFilterGBoxLayout.addWidget(self.throttleComboBox)
        archiveCommentHeaderLayout = QtWidgets.QHBoxLayout(); archiveCommentHeaderLayout.addStretch(); archiveCommentHeaderLayout.addWidget(self.prevArchiveLogBtn); archiveCommentHeaderLayout.addWidget(self.nextArchiveLogBtn)
        self.archiveCommentGBoxLayout.addLayout(archiveCommentHeaderLayout); self.archiveCommentGBoxLayout.addWidget(self.archiveCommentTextEdit)
        
        # --- THIS IS THE RESTORED LEGEND ---
        archive_legend_layout = QtWidgets.QHBoxLayout()
        archive_legend_layout.setSpacing(5)
        archive_legend_layout.addStretch(1)
        for color, text in [("red", "Overload"), ("yellow", "Aged/Very Old"), ("green", "Safe/Less Load")]:
            icon = QtWidgets.QLabel(); icon.setPixmap(self.summary_icons[color]); archive_legend_layout.addWidget(icon)
            label = QtWidgets.QLabel(text); archive_legend_layout.addWidget(label)
            archive_legend_layout.addSpacing(20)
        archive_legend_layout.addStretch(1)

        archiveBtnLayout = QtWidgets.QHBoxLayout()
        archiveBtnLayout.addLayout(archive_legend_layout, 1) # Add legend back
        archiveBtnLayout.addWidget(self.archiveBtn)
        archiveBtnLayout.addWidget(self.cancelArchiveBtn)
        
        self.archiverTabLayout.addWidget(self.archiveProjectGBox); self.archiverTabLayout.addWidget(self.archiveTree); self.archiverTabLayout.addWidget(self.archiveFilterGBox); self.archiverTabLayout.addWidget(self.archiveCommentGBox); self.archiverTabLayout.addLayout(archiveBtnLayout)

        self.baseLayout.addWidget(self.tabWidget)
        
        self._connect_signals(); self._load_config(); self._populate_user_info(); self._populate_project_combos()
        self.clock_timer = QtCore.QTimer(self); self.clock_timer.timeout.connect(self._update_datetime); self.clock_timer.start(1000)


    def _get_resource_path(self, relative_path):
        """
        Get absolute path to resource. First, check for an external file next to the executable.
        If not found, fall back to the bundled file inside the PyInstaller temp folder.
        """
        # Path for the external file (next to the .exe or script)
        if getattr(sys, 'frozen', False): # Running as a bundled exe
            base_path = os.path.dirname(sys.executable)
        else: # Running as a .py script
            # FIX: Use the script's directory, not the current working directory
            base_path = os.path.dirname(__file__)
        
        external_path = os.path.join(base_path, relative_path)
        
        # 1. Prioritize the external file if it exists
        if os.path.exists(external_path):
            return external_path

        # 2. Fallback to the internal, bundled file
        try:
            internal_base_path = sys._MEIPASS
            internal_path = os.path.join(internal_base_path, relative_path)
            if os.path.exists(internal_path):
                return internal_path
        except Exception:
            pass

        return external_path



    def _check_user_permissions(self):
        """Checks if the current user is an admin and enables/disables the Archiver tab."""
        try:
            current_user = os.environ.get('USER') or os.environ.get('USERNAME', 'N/A')
            admin_list = self.config_data.get("admin_users", [])
            
            is_admin = current_user in admin_list
            
            # The Archiver is the second tab (index 1)
            self.tabWidget.setTabEnabled(1, is_admin)
            
            if not is_admin:
                print(f"User '{current_user}' is not in the admin list. Disabling Archiver tab.")
        except Exception as e:
            print(f"Could not verify user permissions: {e}")
            self.tabWidget.setTabEnabled(1, False) # Disable by default on error

    def _create_publisher_legend(self):
        """Creates the layout for the publisher status icon legend."""
        layout = QtWidgets.QHBoxLayout()
        layout.setSpacing(5)
        
        # Publish Status (SL)
        sl_layout = QtWidgets.QHBoxLayout(); sl_layout.setSpacing(3)
        sl_layout.addWidget(QtWidgets.QLabel("<b>SL:</b>"))
        sl_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['grey'])); sl_layout.addWidget(QtWidgets.QLabel("NoData"))
        sl_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['blue'])); sl_layout.addWidget(QtWidgets.QLabel("Not Published"))
        sl_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['red'])); sl_layout.addWidget(QtWidgets.QLabel("Corrupted"))
        sl_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['limeGreen'])); sl_layout.addWidget(QtWidgets.QLabel("Published"))
        
        # Frame Status (SR)
        sr_layout = QtWidgets.QHBoxLayout(); sr_layout.setSpacing(3)
        sr_layout.addWidget(QtWidgets.QLabel("<b>SR:</b>"))
        sr_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['grey'])); sr_layout.addWidget(QtWidgets.QLabel("Scanning"))
        sr_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['teal'])); sr_layout.addWidget(QtWidgets.QLabel("FFR"))
        sr_layout.addWidget(QtWidgets.QLabel(pixmap=self.summary_icons['magenta'])); sr_layout.addWidget(QtWidgets.QLabel("FML, TEST"))

        layout.addLayout(sl_layout)
        separator = QtWidgets.QFrame(); separator.setFrameShape(QtWidgets.QFrame.VLine); separator.setFrameShadow(QtWidgets.QFrame.Sunken); layout.addWidget(separator)
        layout.addLayout(sr_layout)
        return layout

    def _analyze_and_update_summary(self, shot_item):
        """Analyzes all versions within a shot and updates the summary widget."""
        counts = {'red': 0, 'yellow': 0, 'green': 0}
        
        shot_name = shot_item.text(0)
        show_name = self.archiveShowComBox.currentText()
        seq_name = self.archiveSeqComBox.currentText()
        dept = self.config_data.get("active_department")
        dept_paths = self.config_data.get("departments", {}).get(dept, {})
        source_mode = self.archiveDataSourceComBox.currentText()

        try:
            if source_mode == "WIP":
                source_template = dept_paths.get("source_path")
                if not source_template: return
                user_base_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, source_template.replace('/', os.sep))
                if not os.path.exists(user_base_path): return
                
                user_dirs = [d for d in os.listdir(user_base_path) if os.path.isdir(os.path.join(user_base_path, d))]
                for user in user_dirs:
                    preview_path = os.path.join(user_base_path, user, "renders", "preview")
                    if not os.path.exists(preview_path): continue
                    render_names = [r for r in os.listdir(preview_path) if os.path.isdir(os.path.join(preview_path, r))]
                    for render in render_names:
                        version_path = os.path.join(preview_path, render)
                        versions = [v for v in os.listdir(version_path) if os.path.isdir(os.path.join(version_path, v))]
                        for version in versions:
                            full_path = os.path.join(version_path, version)
                            size = self._get_directory_size(full_path)
                            age = self._get_folder_age_in_days(full_path)
                            # FLIPPED LOGIC
                            if size < 5120: counts['green'] += 1
                            elif size >= 5120 and age > self.icon_age_threshold: counts['yellow'] += 1
                            elif size >= 5120 and age <= self.icon_age_threshold: counts['red'] += 1
            
            elif source_mode == "FINAL":
                publish_template = dept_paths.get("publish_path")
                if not publish_template: return
                publish_base_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, publish_template.replace('/', os.sep))
                if not os.path.exists(publish_base_path): return

                render_names = [r for r in os.listdir(publish_base_path) if os.path.isdir(os.path.join(publish_base_path, r))]
                for render in render_names:
                    version_path = os.path.join(publish_base_path, render)
                    versions = [v for v in os.listdir(version_path) if os.path.isdir(os.path.join(version_path, v))]
                    for version in versions:
                        full_path = os.path.join(version_path, version)
                        size = self._get_directory_size(full_path)
                        age = self._get_folder_age_in_days(full_path)
                        # FLIPPED LOGIC
                        if size < 5120: counts['green'] += 1
                        elif size >= 5120 and age > self.icon_age_threshold: counts['yellow'] += 1
                        elif size >= 5120 and age <= self.icon_age_threshold: counts['red'] += 1

        except Exception as e:
            print(f"Error during summary analysis: {e}")

        self.statusSummary.update_summary(counts, self.summary_icons)

    
    def _on_publish_clicked(self):
        selected_items = self.rendersTree.selectedItems();
        if not selected_items: return
        
        is_move = self.move_radio_btn.isChecked()
        copy_jobs, self.published_versions = [], []
        
        show_name, seq_name, shot_name = self.jobComBox.currentText(), self.seqNameComBox.currentText(), self.shotNameComBox.currentText()
        dept = self.config_data.get("active_department")
        dept_paths = self.config_data.get("departments", {}).get(dept, {}); publish_template = dept_paths.get("publish_path")
        if not publish_template: QtWidgets.QMessageBox.critical(self, "Error", f"No 'publish_path' defined for department '{dept}' in config."); return
        
        for item in selected_items:
            # New, simpler way to get the path
            source_path = item.data(0, QtCore.Qt.UserRole)
            if not source_path: continue

            # Reconstruct destination path from source
            path_parts = source_path.split(os.sep)
            render_name = path_parts[-2]
            version_name = path_parts[-1]

            base_shot_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name)
            dest_path = os.path.join(base_shot_path, publish_template.replace('/', os.sep), render_name, version_name)
            
            copy_jobs.append((source_path, dest_path))
            self.published_versions.append({ "source": source_path, "destination": dest_path })
        
        self.progress_dialog = ProgressDialog(self); self.thread = QtCore.QThread()
        self.worker = RobocopyWorker(copy_jobs, is_move)
        self.worker.moveToThread(self.thread); 
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_publish_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.log_message.connect(self.progress_dialog.add_log)
        self.worker.progress_updated.connect(self.progress_dialog.set_progress)
        self.worker.speed_updated.connect(self.progress_dialog.set_speed)
        self.progress_dialog.abort_clicked.connect(self.worker.abort)
        self.progress_dialog.pause_toggled.connect(self.worker.toggle_pause)
        
        self.thread.start(); self.progress_dialog.exec()
    

    def _get_folder_age_in_days(self, path):
        """Returns the age of a folder in days."""
        try:
            # Use time.time() for calculations
            mtime = os.path.getmtime(path)
            age_seconds = time.time() - mtime
            return age_seconds / (24 * 3600) # seconds in a day
        except FileNotFoundError:
            return 0

    def _format_size(self, size_bytes):
        """Formats a size in bytes to a human-readable string (KB, MB, GB...)."""
        if size_bytes < 1024:
            return "0.0 KB"
        
        # Define units and their corresponding byte values
        TB = 1024**4
        GB = 1024**3
        MB = 1024**2
        KB = 1024
        
        if size_bytes >= TB:
            return f"{size_bytes / TB:.1f} TB"
        elif size_bytes >= GB:
            return f"{size_bytes / GB:.1f} GB"
        elif size_bytes >= MB:
            return f"{size_bytes / MB:.1f} MB"
        else: # Must be at least 1 KB
            return f"{size_bytes / KB:.1f} KB"


    # --- NEW ARCHIVER METHODS ---
    def _on_archive_show_selected(self, show_name):
        self.archiveSeqComBox.clear(); self.archiveSeqComBox.addItem("Select Sequence...")
        if show_name and show_name != "Select Show...":
            seq_path = os.path.join(self.show_root_path, show_name, "Production", "Shots")
            try:
                if os.path.exists(seq_path):
                    sequences = [d for d in os.listdir(seq_path) if os.path.isdir(os.path.join(seq_path, d))]; self.archiveSeqComBox.addItems(sorted(sequences))
            except Exception as e: print(f"Error populating sequences for {show_name}: {e}")
        self.archiveSeqComBox.setCurrentIndex(0)

    def _on_archive_seq_selected(self, seq_name):
        self.archiveTree.clear()
        self.statusSummary.reset(self.summary_icons)
        show_name = self.archiveShowComBox.currentText()
        if not all([show_name and show_name != "Select Show...", seq_name and seq_name != "Select Sequence..."]): return
        
        seq_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name)
        
        # Immediately populate tree with placeholders
        try:
            if os.path.exists(seq_path):
                shots = [s for s in os.listdir(seq_path) if os.path.isdir(os.path.join(seq_path, s))]
                for shot_name in sorted(shots):
                    shot_item = QtWidgets.QTreeWidgetItem(self.archiveTree, [shot_name])
                    shot_item.setText(1, "Calculating...")
                    shot_item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    shot_item.addChild(QtWidgets.QTreeWidgetItem(["Loading..."]))
            else:
                return
        except Exception as e:
            print(f"Error populating archive tree with shots: {e}")
            return

        # Get the currently selected data source
        source_mode = self.archiveDataSourceComBox.currentText()

        # Start background scanner to update the sizes
        self.scanner_thread = QtCore.QThread()
        self.scanner_worker = ShotScannerWorker(seq_path, self.config_data, source_mode) # Pass source_mode
        self.scanner_worker.moveToThread(self.scanner_thread)
        
        self.scanner_worker.shot_found.connect(self._update_shot_size_in_tree)
        self.scanner_thread.started.connect(self.scanner_worker.run)
        self.scanner_worker.finished.connect(self.scanner_thread.quit)
        self.scanner_worker.finished.connect(self.scanner_worker.deleteLater)
        self.scanner_thread.finished.connect(self.scanner_thread.deleteLater)
        
        self.scanner_thread.start()

    def _update_shot_size_in_tree(self, shot_name, total_size):
        """Finds a shot item in the tree and updates its size column."""
        items = self.archiveTree.findItems(shot_name, QtCore.Qt.MatchExactly, 0)
        if items:
            shot_item = items[0]
            shot_item.setText(1, self._format_size(total_size))

    def _add_shot_to_archive_tree(self, shot_name, total_size):
        """Slot to receive data from the scanner and add a shot to the tree."""
        shot_item = QtWidgets.QTreeWidgetItem(self.archiveTree, [shot_name])
        shot_item.setText(1, self._format_size(total_size))
        shot_item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        shot_item.addChild(QtWidgets.QTreeWidgetItem(["Loading..."]))


    def _on_archive_item_expanded(self, item):
        if item.parent() is not None or not (item.childCount() == 1 and item.child(0).text(0) == "Loading..."): return
        item.takeChild(0)

        shot_name = item.text(0)
        show_name = self.archiveShowComBox.currentText()
        seq_name = self.archiveSeqComBox.currentText()
        
        dept = self.config_data.get("active_department")
        dept_paths = self.config_data.get("departments", {}).get(dept, {})
        source_mode = self.archiveDataSourceComBox.currentText()

        try:
            if source_mode == "WIP":
                source_template = dept_paths.get("source_path")
                if not source_template: return
                user_base_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, source_template.replace('/', os.sep))
                if not os.path.exists(user_base_path): return
                
                renders = {}
                user_dirs = [d for d in os.listdir(user_base_path) if os.path.isdir(os.path.join(user_base_path, d))]
                for user in user_dirs:
                    preview_path = os.path.join(user_base_path, user, "renders", "preview")
                    if os.path.exists(preview_path):
                        render_names = [r for r in os.listdir(preview_path) if os.path.isdir(os.path.join(preview_path, r))]
                        for render in render_names:
                            if render not in renders: renders[render] = []
                            version_path = os.path.join(preview_path, render)
                            versions = [v for v in os.listdir(version_path) if os.path.isdir(os.path.join(version_path, v))]
                            renders[render].append({'user': user, 'versions': versions, 'path': version_path})

                for render_name in sorted(renders.keys()):
                    render_item = QtWidgets.QTreeWidgetItem(item, [render_name])
                    for user_data in sorted(renders[render_name], key=lambda x: x['user']):
                        for version in sorted(user_data['versions']):
                            version_item = QtWidgets.QTreeWidgetItem(render_item); version_item.setText(0, f"    {version} ({user_data['user']})")
                            full_version_path = os.path.join(user_data['path'], version)
                            size = self._get_directory_size(full_version_path); age = self._get_folder_age_in_days(full_version_path)
                            version_item.setText(1, self._format_size(size)); version_item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                            # FLIPPED LOGIC
                            if size < 5120: version_item.setIcon(2, self.weight_green_icon)
                            elif size >= 5120 and age > self.icon_age_threshold: version_item.setIcon(2, self.weight_yellow_icon)
                            elif size >= 5120 and age <= self.icon_age_threshold: version_item.setIcon(2, self.weight_red_icon)
            
            elif source_mode == "FINAL":
                publish_template = dept_paths.get("publish_path")
                if not publish_template: return
                publish_base_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, publish_template.replace('/', os.sep))
                if not os.path.exists(publish_base_path): return

                render_names = [r for r in os.listdir(publish_base_path) if os.path.isdir(os.path.join(publish_base_path, r))]
                for render_name in sorted(render_names):
                    render_item = QtWidgets.QTreeWidgetItem(item, [render_name])
                    version_path = os.path.join(publish_base_path, render_name)
                    versions = [v for v in os.listdir(version_path) if os.path.isdir(os.path.join(version_path, v))]
                    for version in sorted(versions):
                        version_item = QtWidgets.QTreeWidgetItem(render_item); version_item.setText(0, f"    {version}")
                        full_version_path = os.path.join(version_path, version)
                        size = self._get_directory_size(full_version_path); age = self._get_folder_age_in_days(full_version_path)
                        version_item.setText(1, self._format_size(size)); version_item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                        # FLIPPED LOGIC
                        if size < 5120: version_item.setIcon(2, self.weight_green_icon)
                        elif size >= 5120 and age > self.icon_age_threshold: version_item.setIcon(2, self.weight_yellow_icon)
                        elif size >= 5120 and age <= self.icon_age_threshold: version_item.setIcon(2, self.weight_red_icon)
            
            self._analyze_and_update_summary(item)

        except Exception as e: print(f"Error expanding archive item: {e}")

    def _on_archive_shot_clicked(self, item, column):
        """When a shot is clicked (not just expanded), re-run the analysis."""
        # Only run analysis for top-level shot items
        if item.parent() is None:
            self._analyze_and_update_summary(item)

    # ... (Rest of the methods are unchanged) ...
    def closeEvent(self, event):
        """Ensures the background thread is terminated cleanly on close."""
        # FIX: Check if the thread is a valid QThread instance before checking if it's running
        if hasattr(self, 'thread') and isinstance(self.thread, QtCore.QThread) and self.thread.isRunning():
            self.worker.abort()
            self.thread.quit()
            if not self.thread.wait(5000): # Wait up to 5 seconds
                print("Warning: Robocopy thread did not terminate gracefully.")
        event.accept()
    def _create_icon_from_char(self, char, size=64):
        pixmap = QtGui.QPixmap(size, size); pixmap.fill(QtCore.Qt.transparent); painter = QtGui.QPainter(pixmap); painter.setRenderHint(QtGui.QPainter.Antialiasing)
        font = painter.font(); font.setPixelSize(int(size * 0.8)); painter.setFont(font); painter.setPen(QtGui.QColor("#f0f0f0")); painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, char); painter.end()
        return QtGui.QIcon(pixmap)
    def _create_icons(self):
        # Publish status icons
        self.green_dot_icon = self._create_color_icon(QtGui.QColor("#7CFC00"))
        self.blue_dot_icon = self._create_color_icon(QtGui.QColor("#4682B4"))
        self.red_dot_icon = self._create_color_icon(QtGui.QColor("red"))
        
        # Frame status icons
        self.magenta_icon = self._create_color_icon(QtGui.QColor("magenta"))
        self.teal_icon = self._create_color_icon(QtGui.QColor("teal"))
        
        # Shared grey icon
        self.grey_icon = self._create_color_icon(QtGui.QColor("grey"))
        
        # Data weight icons
        self.weight_red_icon = self._create_color_icon(QtGui.QColor("#DC143C"))
        self.weight_yellow_icon = self._create_color_icon(QtGui.QColor("#FFD700"))
        self.weight_green_icon = self._create_color_icon(QtGui.QColor("#32CD32"))

        # Icons for summary/legend widgets (pixmaps for direct use in QLabel)
        self.summary_icons = {
            'red': self._create_color_pixmap(QtGui.QColor("#DC143C")),
            'yellow': self._create_color_pixmap(QtGui.QColor("#FFD700")),
            'green': self._create_color_pixmap(QtGui.QColor("#32CD32")),
            'limeGreen': self._create_color_pixmap(QtGui.QColor("#7CFC00")),
            'red_dim': self._create_color_pixmap(QtGui.QColor("#DC143C"), 0.3),
            'yellow_dim': self._create_color_pixmap(QtGui.QColor("#FFD700"), 0.3),
            'green_dim': self._create_color_pixmap(QtGui.QColor("#32CD32"), 0.3),
            'grey': self._create_color_pixmap(QtGui.QColor("grey")),
            'blue': self._create_color_pixmap(QtGui.QColor("#4682B4")),
            'teal': self._create_color_pixmap(QtGui.QColor("teal")),
            'magenta': self._create_color_pixmap(QtGui.QColor("magenta"))
        }


    def _create_color_pixmap(self, color, opacity=1.0):
        """Helper function to generate a pixmap of a colored circle with opacity."""
        pixmap = QtGui.QPixmap(16, 16); pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap); painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setOpacity(opacity) # Set opacity
        painter.setBrush(color); painter.setPen(QtCore.Qt.NoPen); painter.drawEllipse(4, 4, 8, 8); painter.end()
        return pixmap

    def _create_color_icon(self, color):
        pixmap = QtGui.QPixmap(16, 16); pixmap.fill(QtCore.Qt.transparent); painter = QtGui.QPainter(pixmap); painter.setRenderHint(QtGui.QPainter.Antialiasing); painter.setBrush(color); painter.setPen(QtCore.Qt.NoPen); painter.drawEllipse(4, 4, 8, 8); painter.end()
        return QtGui.QIcon(pixmap)
    def _get_directory_size(self, path):
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp): total_size += os.path.getsize(fp)
        except FileNotFoundError: return 0
        return total_size
    def _connect_signals(self):
        self.load_config_action.triggered.connect(self._on_config_clicked); self.how_to_action.triggered.connect(self._show_how_to); self.release_notes_action.triggered.connect(self._show_release_notes)
        self.cancelBtn.clicked.connect(self.close)
        
        # Publisher signals
        self.publishBtn.clicked.connect(self._on_publish_clicked)
        self.jobComBox.currentTextChanged.connect(self._on_show_selected)
        self.seqNameComBox.currentTextChanged.connect(self._on_seq_selected)
        self.shotNameComBox.currentTextChanged.connect(self._on_shot_selected)
        self.rendersTree.itemSelectionChanged.connect(self._update_publish_button_state); self.commentTextEdit.textChanged.connect(self._update_publish_button_state); self.rendersTree.itemExpanded.connect(self._on_item_expanded)
        self.prevLogBtn.clicked.connect(self._browse_prev_log); self.nextLogBtn.clicked.connect(self._browse_next_log)

        # Archiver signals
        self.archiveShowComBox.currentTextChanged.connect(self._on_archive_show_selected)
        self.archiveSeqComBox.currentTextChanged.connect(self._on_archive_seq_selected)
        self.archiveDataSourceComBox.currentTextChanged.connect(self._on_archive_seq_selected) # New connection
        self.archiveTree.itemExpanded.connect(self._on_archive_item_expanded)
        self.archiveTree.itemClicked.connect(self._on_archive_shot_clicked)
        self.maxAgeRadioButton.toggled.connect(self.maxAgeLineEdit.setEnabled)
        self.archiveBtn.clicked.connect(self._on_archive_clicked)
        self.archiveTree.itemSelectionChanged.connect(self._update_archive_button_state)
        self.archiveCommentTextEdit.textChanged.connect(self._update_archive_button_state)
        self.prevArchiveLogBtn.clicked.connect(self._browse_prev_archive_log)
        self.nextArchiveLogBtn.clicked.connect(self._browse_next_archive_log)
        self.cancelArchiveBtn.clicked.connect(self.close)
        




    def _load_config(self, config_path=None):
        """Loads settings from a specified or default config file."""
        if config_path is None:
            config_path = self._get_resource_path("xPubConfig.JSON") # Use the helper
        try:
            with open(config_path, 'r', encoding="utf-8") as f: 
                self.config_data = json.load(f)
            
            self.show_root_path = self.config_data.get("project_root", "")
            self.icon_age_threshold = self.config_data.get("icon_age_threshold", 30)
            
            if "project_root" not in self.config_data or "active_department" not in self.config_data: 
                raise KeyError("Config must contain 'project_root' and 'active_department' keys.")
                
            print(f"Config loaded successfully from: {config_path}")
            print(f"Icon Age Threshold set to: {self.icon_age_threshold} days")
            
            self._check_user_permissions()
            self._populate_project_combos()

        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            self.show_root_path = ""; self.config_data = {}
            self.icon_age_threshold = 30
            QtWidgets.QMessageBox.warning(self, "Config Error", f"Could not load or parse config file.\n{e}")
            self._check_user_permissions()
    
    def _on_config_clicked(self):
        script_dir = os.path.dirname(__file__); file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Config File", script_dir, "JSON Files (*.json)")
        if file_path: self._load_config(file_path)
    def _show_how_to(self): self._show_help_dialog("HowToOperate.JSON", "How To Operate")
    def _show_release_notes(self): self._show_help_dialog("ReleaseNotes.JSON", "Release Notes")

    def _show_help_dialog(self, file_name, title):
        try:
            file_path = self._get_resource_path(file_name) # Use the helper
            with open(file_path, 'r', encoding="utf-8") as f: data = json.load(f)
            content = data.get("content", "Error: Could not find content in file."); dialog = InfoDialog(title, content, self); dialog.exec()
        except FileNotFoundError: QtWidgets.QMessageBox.warning(self, "File Not Found", f"Could not find the required file:\n{file_name}")
        except Exception as e: QtWidgets.QMessageBox.critical(self, "Error", f"An error occurred while loading help content:\n{e}")
    
    def _update_publish_button_state(self):
        if self.rendersTree.hasFocus(): self._reset_log_browser()
        versions_selected = bool(self.rendersTree.selectedItems()); comment_exists = bool(self.commentTextEdit.toPlainText().strip())
        self.publishBtn.setEnabled(versions_selected and comment_exists); self.move_radio_btn.setEnabled(versions_selected); self.daily_check_box.setEnabled(versions_selected)
    def _populate_user_info(self):
        artist = os.environ.get('USER') or os.environ.get('USERNAME', 'N/A'); host = os.environ.get('HOSTNAME') or os.environ.get('COMPUTERNAME', 'N/A')
        formatted_artist = artist.replace('.', '_'); self.artistLineEdit.setText(formatted_artist); self.deptLineEdit.setText(host); self._update_datetime()
    def _update_datetime(self):
        current_datetime = datetime.datetime.now().strftime('%d %b %Y %H:%M:%S'); self.dateLineEdit.setText(current_datetime)

    def _populate_project_combos(self):
        # FIX: Explicitly pass the target combo box to the function
        self._populate_shows(self.jobComBox)
        self._populate_shows(self.archiveShowComBox)
        

    def _populate_shows(self, combo_box):
        combo_box.clear(); combo_box.addItem("Select Show...")
        try:
            if self.show_root_path and os.path.exists(self.show_root_path):
                shows = [d for d in os.listdir(self.show_root_path) if os.path.isdir(os.path.join(self.show_root_path, d))]; combo_box.addItems(sorted(shows))
        except Exception as e: print(f"Error populating shows: {e}")
    def _on_show_selected(self, show_name):
        self.seqNameComBox.clear(); self.seqNameComBox.addItem("Select Sequence...")
        if show_name and show_name != "Select Show...":
            seq_path = os.path.join(self.show_root_path, show_name, "Production", "Shots")
            try:
                if os.path.exists(seq_path):
                    sequences = [d for d in os.listdir(seq_path) if os.path.isdir(os.path.join(seq_path, d))]; self.seqNameComBox.addItems(sorted(sequences))
            except Exception as e: print(f"Error populating sequences for {show_name}: {e}")
        self.seqNameComBox.setCurrentIndex(0)
    def _on_seq_selected(self, seq_name):
        self.shotNameComBox.clear(); self.shotNameComBox.addItem("Select Shot...")
        show_name = self.jobComBox.currentText()
        if seq_name and seq_name != "Select Sequence..." and show_name != "Select Show...":
            shot_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name)
            try:
                if os.path.exists(shot_path):
                    shots = [d for d in os.listdir(shot_path) if os.path.isdir(os.path.join(shot_path, d))]; self.shotNameComBox.addItems(sorted(shots))
            except Exception as e: print(f"Error populating shots for {seq_name}: {e}")
    def _on_shot_selected(self, shot_name):
        self.rendersTree.clear(); self._reset_log_browser(); self._load_shot_logs(shot_name)
        
        dept = self.config_data.get("active_department")
        if not dept: self.rendersTree.clear(); return
        dept_paths = self.config_data.get("departments", {}).get(dept, {}); source_template = dept_paths.get("source_path")
        if not source_template: return
        
        show_name, seq_name = self.jobComBox.currentText(), self.seqNameComBox.currentText()
        if not all(s and "Select" not in s for s in [show_name, seq_name, shot_name]): return
        
        user_base_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, source_template.replace('/', os.sep))

        try:
            if not os.path.exists(user_base_path): return
            
            layers_data = {}
            user_dirs = [d for d in os.listdir(user_base_path) if os.path.isdir(os.path.join(user_base_path, d))]
            for user in user_dirs:
                preview_path = os.path.join(user_base_path, user, "renders", "preview")
                if not os.path.exists(preview_path): continue
                
                render_names = [r for r in os.listdir(preview_path) if os.path.isdir(os.path.join(preview_path, r))]
                for render_name in render_names:
                    if render_name not in layers_data:
                        layers_data[render_name] = []
                    
                    version_path = os.path.join(preview_path, render_name)
                    versions = [v for v in os.listdir(version_path) if os.path.isdir(os.path.join(version_path, v))]
                    for version in versions:
                        full_path = os.path.join(version_path, version)
                        mtime = os.path.getmtime(full_path)
                        layers_data[render_name].append({
                            'user': user, 'version': version, 'path': full_path, 'mtime': mtime
                        })

            for layer_name in sorted(layers_data.keys()):
                layer_item = QtWidgets.QTreeWidgetItem(self.rendersTree, [layer_name])
                layer_item.setFlags(layer_item.flags() & ~QtCore.Qt.ItemIsSelectable)

                sorted_versions = sorted(layers_data[layer_name], key=lambda x: x['mtime'], reverse=True)

                for version_data in sorted_versions:
                    version_item = QtWidgets.QTreeWidgetItem(layer_item)
                    version_item.setText(0, f"    {version_data['version']} ({version_data['user']})")
                    
                    date_str = datetime.datetime.fromtimestamp(version_data['mtime']).strftime('%d %b %Y %H:%M')
                    version_item.setText(1, date_str)
                    version_item.setTextAlignment(1, QtCore.Qt.AlignCenter)
                    
                    version_item.setData(0, QtCore.Qt.UserRole, version_data['path'])

                    self._set_publisher_item_icons(version_item, version_data, layer_name)
                
                # layer_item.setExpanded(True) # <-- THIS LINE IS NOW REMOVED

        except Exception as e: print(f"Error finding user directories: {e}")

    def _set_publisher_item_icons(self, version_item, version_data, render_name):
        """Sets the publish and frame status icons for a version item in the publisher tree."""
        source_version_path = version_data['path']
        
        # --- Frame Status Icon (column 2) ---
        frame_status = self._frame_validation(source_version_path)
        if frame_status == "MATCH": version_item.setIcon(2, self.teal_icon)
        elif frame_status == "MISMATCH": version_item.setIcon(2, self.magenta_icon)
        else: version_item.setIcon(2, self.grey_icon)

        # --- Publish Status Icon (column 2, added next to frame status) ---
        show_name, seq_name, shot_name = self.jobComBox.currentText(), self.seqNameComBox.currentText(), self.shotNameComBox.currentText()
        dept = self.config_data.get("active_department")
        dept_paths = self.config_data.get("departments", {}).get(dept, {}); publish_template = dept_paths.get("publish_path")
        if not publish_template: return

        base_shot_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name)
        publish_check_path = os.path.join(base_shot_path, publish_template.replace('/', os.sep), render_name, version_data['version'])
        
        source_size = self._get_directory_size(source_version_path)
        if source_size == 0:
            version_item.setIcon(0, self.grey_icon)
            return

        if os.path.exists(publish_check_path):
            publish_size = self._get_directory_size(publish_check_path)
            if publish_size < source_size: version_item.setIcon(0, self.red_dot_icon)
            else: version_item.setIcon(0, self.green_dot_icon)
        else:
            version_item.setIcon(0, self.blue_dot_icon)
    
    
    def _frame_validation(self, source_path): return "NO_DATA"


    def _on_item_expanded(self, item):
        # This function now ONLY handles the archiver tree's lazy loading
        if self.sender() != self.archiveTree:
            return

        if item.parent() is not None or not (item.childCount() == 1 and item.child(0).text(0) == "Loading..."): return
        
        # ... (rest of the archiver expansion logic remains unchanged) ...
        item.takeChild(0)

        shot_name = item.text(0)
        show_name = self.archiveShowComBox.currentText()
        seq_name = self.archiveSeqComBox.currentText()
        
        dept = self.config_data.get("active_department")
        dept_paths = self.config_data.get("departments", {}).get(dept, {}); source_template = dept_paths.get("source_path")
        if not source_template: return
        
        user_base_path = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, source_template.replace('/', os.sep))

        try:
            pass
            # ... (rest of the archiver expansion logic remains unchanged) ...
        except Exception as e: print(f"Error expanding archive item: {e}")
    
    def _load_shot_logs(self, shot_name):
        self.shot_logs = []
        show_name, seq_name = self.jobComBox.currentText(), self.seqNameComBox.currentText()
        if not all(s and "Select" not in s for s in [show_name, seq_name, shot_name]): self._update_log_browser_state(); return
        log_file = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, "data", "lighting", "xPubLog.JSON")
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f: self.shot_logs = json.load(f)
        except Exception as e: print(f"Could not load or parse log file: {e}")
        self._update_log_browser_state()
    def _update_log_browser_state(self):
        num_logs = len(self.shot_logs)
        can_go_prev = num_logs > 0 and self.current_shot_log_index != 0; can_start_browsing = num_logs > 0 and self.current_shot_log_index == -1
        self.prevLogBtn.setEnabled(can_go_prev or can_start_browsing); self.nextLogBtn.setEnabled(self.current_shot_log_index != -1)
    def _browse_prev_log(self):
        if not self.shot_logs: return
        if self.current_shot_log_index == -1: self.current_shot_log_index = len(self.shot_logs) - 1
        elif self.current_shot_log_index > 0: self.current_shot_log_index -= 1
        self._display_log_entry(); self._update_log_browser_state()
    def _browse_next_log(self):
        if not self.shot_logs or self.current_shot_log_index == -1: return
        if self.current_shot_log_index < len(self.shot_logs) - 1: self.current_shot_log_index += 1
        else: self.current_shot_log_index = -1
        self._display_log_entry(); self._update_log_browser_state()
    def _reset_log_browser(self):
        if self.current_shot_log_index != -1: self.current_shot_log_index = -1; self._display_log_entry(); self._update_log_browser_state()
    def _display_log_entry(self):
        if self.current_shot_log_index == -1: self.commentTextEdit.setReadOnly(False); self.commentTextEdit.clear(); self.commentTextEdit.setPlaceholderText("Add your Comment. Note: Without a Comment, Release won't work."); return
        self.commentTextEdit.setReadOnly(True)
        try:
            entry = self.shot_logs[self.current_shot_log_index]
            display_text = f"DateTime: {entry.get('DateTime', 'N/A')}\n"
            display_text += f"User: {entry.get('User', 'N/A')}\n\n"
            display_text += f"Comment:\n{entry.get('Comment', 'N/A')}\n\n"
            display_text += "Published Versions:\n"
            for pub in entry.get('Publishes', []):
                source_name = os.path.basename(os.path.dirname(pub.get('source'))); version_name = os.path.basename(pub.get('source'))
                display_text += f"  - {source_name}/{version_name}\n"
            self.commentTextEdit.setText(display_text)
        except IndexError: self.commentTextEdit.setText("Error: Could not retrieve log entry.")
    def _on_publish_finished(self, success):
        self.progress_dialog.on_finished(success, "PUBLISH COMPLETED SUCCESSFULLY", "PUBLISH FAILED OR ABORTED")
        if success: self._create_publish_log(); self._on_shot_selected(self.shotNameComBox.currentText())
    def _create_publish_log(self):
        show_name, seq_name, shot_name = self.jobComBox.currentText(), self.seqNameComBox.currentText(), self.shotNameComBox.currentText()
        log_dir = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, shot_name, "data", "lighting")
        log_file = os.path.join(log_dir, "xPubLog.JSON")
        mode = "Move" if self.move_radio_btn.isChecked() else "Copy"
        new_entry = { "User": self.artistLineEdit.text(), "Host": self.deptLineEdit.text(), "DateTime": self.dateLineEdit.text(), "Mode": mode, "Comment": self.commentTextEdit.toPlainText(), "Publishes": self.published_versions }
        try:
            os.makedirs(log_dir, exist_ok=True); all_logs = []
            if os.path.exists(log_file):
                os.chmod(log_file, stat.S_IWRITE)
                with open(log_file, 'r', encoding="utf-8") as f: # FIX: Specify encoding
                    try:
                        all_logs = json.load(f)
                        if not isinstance(all_logs, list): all_logs = [all_logs]
                    except json.JSONDecodeError: all_logs = []
            all_logs.append(new_entry)
            with open(log_file, 'w', encoding="utf-8") as f: # FIX: Specify encoding
                json.dump(all_logs, f, indent=4)
            os.chmod(log_file, stat.S_IREAD)
            self.progress_dialog.add_log(f"Successfully updated log file: {log_file}")
        except Exception as e: self.progress_dialog.add_log(f"ERROR: Could not create or write to log file: {e}")

    def _update_archive_button_state(self):
        """Enables the archive button if one or more shots are selected and comment exists."""
        selected_items = self.archiveTree.selectedItems()
        
        # Check if there's at least one selection and ALL are top-level shot items
        is_valid_selection = False
        if selected_items:
            is_valid_selection = all(item.parent() is None for item in selected_items)
            
        comment_exists = bool(self.archiveCommentTextEdit.toPlainText().strip())
        self.archiveBtn.setEnabled(is_valid_selection and comment_exists)

    def _on_archive_clicked(self):
        """Starts the archive process for all selected shots."""
        selected_items = self.archiveTree.selectedItems()
        if not selected_items: return

        show_name = self.archiveShowComBox.currentText()
        seq_name = self.archiveSeqComBox.currentText()
        
        # Collect all shot paths from the selection
        shot_paths = [os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name, item.text(0)) for item in selected_items]

        threshold = self.thresholdSpinBox.value()
        max_age_enabled = self.maxAgeRadioButton.isChecked()
        max_age_days = float('inf')
        if max_age_enabled:
            try:
                max_age_days = int(self.maxAgeLineEdit.text())
            except (ValueError, TypeError):
                QtWidgets.QMessageBox.warning(self, "Invalid Input", "Max Age must be a valid number of days.")
                return

        self.progress_dialog = ProgressDialog(self); self.progress_dialog.setWindowTitle("Archiving...")
        self.thread = QtCore.QThread()
        self.worker = ArchiveWorker(shot_paths, threshold, max_age_days, max_age_enabled, self.config_data)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_archive_finished)
        self.worker.archive_summary_ready.connect(self._create_archive_log)
        self.worker.finished.connect(self.thread.quit); self.worker.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread.deleteLater)
        self.worker.log_message.connect(self.progress_dialog.add_log)
        self.worker.progress_updated.connect(self.progress_dialog.set_progress)
        self.progress_dialog.abort_clicked.connect(self.worker.abort)
        self.progress_dialog.pause_button.setVisible(False) 

        self.thread.start()
        self.progress_dialog.exec()
        
        # Refresh the view for all selected items
        for item in selected_items:
            if item.isExpanded():
                self._on_archive_shot_clicked(item, 0)

    def _on_archive_finished(self, success):
        self.progress_dialog.on_finished(success, "ARCHIVE COMPLETED SUCCESSFULLY", "ARCHIVE FAILED OR ABORTED")

    def _create_archive_log(self, cleaned_versions):
        show_name = self.archiveShowComBox.currentText()
        seq_name = self.archiveSeqComBox.currentText()
        seq_name_with_suffix = f"{seq_name}_Seq"
        log_dir = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name_with_suffix, "data", "lighting")
        log_file = os.path.join(log_dir, "xPubArchiveLog.JSON")
        
        new_entry = { 
            "User": self.artistLineEdit.text(), "Host": self.deptLineEdit.text(), "DateTime": self.dateLineEdit.text(), 
            "Shot": self.archiveTree.selectedItems()[0].text(0),
            "Filters": {
                "Threshold": self.thresholdSpinBox.value(),
                "MaxAge": { "enabled": self.maxAgeRadioButton.isChecked(), "days": self.maxAgeLineEdit.text() if self.maxAgeRadioButton.isChecked() else None },
                "Throttle": self.throttleComboBox.currentText()
            },
            "Comment": self.archiveCommentTextEdit.toPlainText(), "CleanedVersions": cleaned_versions
        }
        
        try:
            os.makedirs(log_dir, exist_ok=True); all_logs = []
            if os.path.exists(log_file):
                os.chmod(log_file, stat.S_IWRITE)
                with open(log_file, 'r', encoding="utf-8") as f: # FIX: Specify encoding
                    try:
                        all_logs = json.load(f)
                        if not isinstance(all_logs, list): all_logs = [all_logs]
                    except json.JSONDecodeError: all_logs = []
            all_logs.append(new_entry)
            with open(log_file, 'w', encoding="utf-8") as f: # FIX: Specify encoding
                json.dump(all_logs, f, indent=4)
            os.chmod(log_file, stat.S_IREAD)
        except Exception as e: 
            print(f"ERROR: Could not create or write to archive log file: {e}")


    def _load_archive_logs(self, seq_name):
        self.archive_logs = []
        show_name = self.archiveShowComBox.currentText()
        if not all([show_name and show_name != "Select Show...", seq_name and seq_name != "Select Sequence..."]):
            self._update_archive_log_browser_state()
            return

        # FIX: Re-added _Seq suffix to the sequence name for the path
        seq_name_with_suffix = f"{seq_name}_Seq"
        log_file = os.path.join(self.show_root_path, show_name, "Production", "Shots", seq_name_with_suffix, "data", "lighting", "xPubArchiveLog.JSON")
        
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    self.archive_logs = json.load(f)
        except Exception as e:
            print(f"Could not load or parse archive log file: {e}")
        
        self._update_archive_log_browser_state()

    def _update_archive_log_browser_state(self):
        num_logs = len(self.archive_logs)
        can_go_prev = num_logs > 0 and self.current_archive_log_index != 0
        can_start_browsing = num_logs > 0 and self.current_archive_log_index == -1
        
        self.prevArchiveLogBtn.setEnabled(can_go_prev or can_start_browsing)
        self.nextArchiveLogBtn.setEnabled(self.current_archive_log_index != -1)

    def _browse_prev_archive_log(self):
        if not self.archive_logs: return
        
        if self.current_archive_log_index == -1:
            self.current_archive_log_index = len(self.archive_logs) - 1
        elif self.current_archive_log_index > 0:
            self.current_archive_log_index -= 1
        
        self._display_archive_log_entry()
        self._update_archive_log_browser_state()

    def _browse_next_archive_log(self):
        if not self.archive_logs or self.current_archive_log_index == -1: return

        if self.current_archive_log_index < len(self.archive_logs) - 1:
            self.current_archive_log_index += 1
        else:
            self.current_archive_log_index = -1

        self._display_archive_log_entry()
        self._update_archive_log_browser_state()

    def _display_archive_log_entry(self):
        if self.current_archive_log_index == -1:
            self.archiveCommentTextEdit.setReadOnly(False)
            self.archiveCommentTextEdit.clear()
            self.archiveCommentTextEdit.setPlaceholderText("Add comments for the archive operation...")
            return

        self.archiveCommentTextEdit.setReadOnly(True)
        try:
            entry = self.archive_logs[self.current_archive_log_index]
            
            # Build the new, detailed display string
            display_text = f"DateTime: {entry.get('DateTime', 'N/A')}\n"
            display_text += f"User: {entry.get('User', 'N/A')}\n"
            display_text += f"Shot: {entry.get('Shot', 'N/A')}\n\n"

            # Add Filters section
            filters = entry.get('Filters', {})
            display_text += "Filters Used:\n"
            display_text += f"  - Threshold: {filters.get('Threshold', 'N/A')}\n"
            max_age = filters.get('MaxAge', {})
            if max_age.get('enabled'):
                display_text += f"  - Max Age: {max_age.get('days', 'N/A')} Days\n"
            else:
                display_text += "  - Max Age: Disabled\n"
            display_text += f"  - Throttle: {filters.get('Throttle', 'N/A')}\n\n"
            
            display_text += f"Comment:\n{entry.get('Comment', 'N/A')}\n\n"
            
            # Add Cleaned Versions section
            cleaned = entry.get('CleanedVersions', [])
            if cleaned:
                display_text += "Cleaned Versions:\n"
                for version in cleaned:
                    display_text += f"  - {version}\n"
            else:
                display_text += "Cleaned Versions: None\n"

            self.archiveCommentTextEdit.setText(display_text)
        except IndexError:
            self.archiveCommentTextEdit.setText("Error: Could not retrieve log entry.")
# /////////////////////////////////////////////
# STYLESHEET AND LAUNCHER
# \\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
CSS_STYLE = """
/* ================================
   DARK THEME
   ================================ */
QWidget { font-family: "Segoe UI Variable"; font-size: 8pt; background-color: #3c3c3c; color: #f0f0f0; }
QLineEdit { background-color: #2c2c2c; border: 1px solid #5a5a5a; padding: 2px; border-radius: 3px; }
QLineEdit[readOnly="true"] { background-color: #2c2c2c; border: 1px solid #444444; color: #a0a0a0; font-weight: normal; }
QProgressBar { border: 1px solid #444444; border-radius: 5px; background-color: #2c2c2c; max-height: 10px; text-align: center; }
QProgressBar::chunk { background-color: #f39c12; border-radius: 5px; }
QTreeView { alternate-background-color: #363636; background-color: #3c3c3c; border: 1px solid #444444; border-radius: 3px; }
QHeaderView::section { background-color: #5a5a5a; padding: 4px; border: 1px solid #3c3c3c; }
QTreeView::item:hover { background-color: #6a4a29; }
QTreeView::item:selected { background-color: #f39c12; color: #000000; }
QTextEdit { background-color: #2c2c2c; border: 1px solid #5a5a5a; padding: 4px; border-radius: 3px; }
QComboBox QAbstractItemView::item:selected { background-color: #f39c12; color: #000000; }
QTabWidget::pane { border-top: 1px solid #5a5a5a; }
QTabBar::tab { background: transparent; border: none; padding: 8px 15px; color: #a0a0a0; border-bottom: 2px solid transparent; }
QTabBar::tab:!selected:hover { color: #f0f0f0; background-color: #4a4a4a; }
QTabBar::tab:selected { color: #f39c12; border-bottom: 2px solid #f39c12; }
QGroupBox { border: 1px solid #444444; border-radius: 4px; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 3px; }
QPushButton { background-color: #5a5a5a; border: 1px solid #6a6a6a; padding: 5px; border-radius: 3px; min-width: 80px; }
QPushButton:hover { background-color: #6a6a6a; }
QPushButton:pressed { background-color: #7a7a7a; }
QPushButton:disabled { background-color: #2a2a2a; color: #5a5a5a; }
#configButton { min-width: 30px; font-size: 14pt; }
QMenuBar { background-color: #3c3c3c; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background-color: #5a5a5a; }
QMenu { background-color: #2c2c2c; border: 1px solid #5a5a5a; }
QMenu::item:selected { background-color: #f39c12; color: #000000; }
QRadioButton, QCheckBox { spacing: 5px; }
QRadioButton:hover, QCheckBox:hover { color: #ffffff; }
QRadioButton:disabled, QCheckBox:disabled { color: #5a5a5a; }
QRadioButton::indicator, QCheckBox::indicator { width: 14px; height: 14px; background-color: #2c2c2c; border: 1px solid #5a5a5a; }
QRadioButton::indicator:hover, QCheckBox::indicator:hover { border: 1px solid #f39c12; }
QRadioButton::indicator:disabled, QCheckBox::indicator:disabled { background-color: #2a2a2a; border: 1px solid #444444; }
QRadioButton::indicator { border-radius: 7px; }
QRadioButton::indicator:checked { border: 1px solid #f39c12; background-color: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.4, fx: 0.5, fy: 0.5, stop: 0.6 #f39c12, stop: 0.7 #2c2c2c); }
QCheckBox::indicator { border-radius: 3px; }
QCheckBox::indicator:checked { background-color: #f39c12; border: 1px solid #f39c12; }
"""

if __name__ == "__main__":
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)
    
    app.setStyle("Fusion")
    app.setStyleSheet(CSS_STYLE)
    
    window = mainWindow()
    window.show()
    
    sys.exit(app.exec())