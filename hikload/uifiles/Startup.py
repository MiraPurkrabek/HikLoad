# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'Startup.ui'
#
# Created by: PyQt5 UI code generator 5.15.6
#
# WARNING: Any manual changes made to this file will be lost when pyuic5 is
# run again.  Do not edit this file unless you know what you are doing.


from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_Startup(object):
    def setupUi(self, Startup):
        Startup.setObjectName("Startup")
        Startup.resize(927, 752)
        self.gridLayout_2 = QtWidgets.QGridLayout(Startup)
        self.gridLayout_2.setObjectName("gridLayout_2")
        self.gridLayout = QtWidgets.QGridLayout()
        self.gridLayout.setObjectName("gridLayout")
        self.username = QtWidgets.QLineEdit(Startup)
        self.username.setObjectName("username")
        self.gridLayout.addWidget(self.username, 1, 2, 1, 1)
        self.label = QtWidgets.QLabel(Startup)
        self.label.setObjectName("label")
        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)
        self.server_ip = QtWidgets.QLineEdit(Startup)
        self.server_ip.setObjectName("server_ip")
        self.gridLayout.addWidget(self.server_ip, 0, 2, 1, 1)
        self.label_2 = QtWidgets.QLabel(Startup)
        self.label_2.setObjectName("label_2")
        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)
        self.password = QtWidgets.QLineEdit(Startup)
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password.setObjectName("password")
        self.gridLayout.addWidget(self.password, 3, 2, 1, 1)
        self.label_6 = QtWidgets.QLabel(Startup)
        self.label_6.setObjectName("label_6")
        self.gridLayout.addWidget(self.label_6, 4, 0, 1, 1)
        self.label_3 = QtWidgets.QLabel(Startup)
        self.label_3.setObjectName("label_3")
        self.gridLayout.addWidget(self.label_3, 3, 0, 1, 1)
        self.horizontalLayout_2 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_2.setObjectName("horizontalLayout_2")
        self.downloads_folder = QtWidgets.QLineEdit(Startup)
        self.downloads_folder.setEchoMode(QtWidgets.QLineEdit.Normal)
        self.downloads_folder.setObjectName("downloads_folder")
        self.horizontalLayout_2.addWidget(self.downloads_folder)
        self.downloads_folder_button = QtWidgets.QPushButton(Startup)
        self.downloads_folder_button.setMaximumSize(QtCore.QSize(40, 16777215))
        self.downloads_folder_button.setObjectName("downloads_folder_button")
        self.horizontalLayout_2.addWidget(self.downloads_folder_button)
        self.gridLayout.addLayout(self.horizontalLayout_2, 4, 2, 1, 1)
        self.gridLayout_2.addLayout(self.gridLayout, 0, 0, 1, 2)
        spacerItem = QtWidgets.QSpacerItem(318, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem, 1, 0, 1, 1)
        self.horizontalLayout_3 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_3.setObjectName("horizontalLayout_3")
        self.verticalLayout_2 = QtWidgets.QVBoxLayout()
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.groupBox = QtWidgets.QGroupBox(Startup)
        self.groupBox.setObjectName("groupBox")
        self.formLayout = QtWidgets.QFormLayout(self.groupBox)
        self.formLayout.setObjectName("formLayout")
        self.label_4 = QtWidgets.QLabel(self.groupBox)
        self.label_4.setObjectName("label_4")
        self.formLayout.setWidget(0, QtWidgets.QFormLayout.LabelRole, self.label_4)
        self.folder_behavior = QtWidgets.QComboBox(self.groupBox)
        self.folder_behavior.setPlaceholderText("")
        self.folder_behavior.setObjectName("folder_behavior")
        self.folder_behavior.addItem("")
        self.folder_behavior.addItem("")
        self.folder_behavior.addItem("")
        self.folder_behavior.addItem("")
        self.folder_behavior.addItem("")
        self.formLayout.setWidget(0, QtWidgets.QFormLayout.FieldRole, self.folder_behavior)
        self.label_9 = QtWidgets.QLabel(self.groupBox)
        self.label_9.setObjectName("label_9")
        self.formLayout.setWidget(1, QtWidgets.QFormLayout.LabelRole, self.label_9)
        self.download_type = QtWidgets.QComboBox(self.groupBox)
        self.download_type.setPlaceholderText("")
        self.download_type.setObjectName("download_type")
        self.download_type.addItem("")
        self.download_type.addItem("")
        self.formLayout.setWidget(1, QtWidgets.QFormLayout.FieldRole, self.download_type)
        self.label_5 = QtWidgets.QLabel(self.groupBox)
        self.label_5.setObjectName("label_5")
        self.formLayout.setWidget(2, QtWidgets.QFormLayout.LabelRole, self.label_5)
        self.video_format = QtWidgets.QComboBox(self.groupBox)
        self.video_format.setPlaceholderText("")
        self.video_format.setObjectName("video_format")
        self.video_format.addItem("")
        self.video_format.addItem("")
        self.video_format.addItem("")
        self.formLayout.setWidget(2, QtWidgets.QFormLayout.FieldRole, self.video_format)
        self.label_7 = QtWidgets.QLabel(self.groupBox)
        self.label_7.setObjectName("label_7")
        self.formLayout.setWidget(3, QtWidgets.QFormLayout.LabelRole, self.label_7)
        self.start_date = QtWidgets.QDateTimeEdit(self.groupBox)
        self.start_date.setDate(QtCore.QDate(2021, 12, 29))
        self.start_date.setCalendarPopup(True)
        self.start_date.setTimeSpec(QtCore.Qt.UTC)
        self.start_date.setObjectName("start_date")
        self.formLayout.setWidget(3, QtWidgets.QFormLayout.FieldRole, self.start_date)
        self.label_8 = QtWidgets.QLabel(self.groupBox)
        self.label_8.setObjectName("label_8")
        self.formLayout.setWidget(4, QtWidgets.QFormLayout.LabelRole, self.label_8)
        self.end_date = QtWidgets.QDateTimeEdit(self.groupBox)
        self.end_date.setDate(QtCore.QDate(2022, 12, 28))
        self.end_date.setCalendarPopup(True)
        self.end_date.setTimeSpec(QtCore.Qt.UTC)
        self.end_date.setObjectName("end_date")
        self.formLayout.setWidget(4, QtWidgets.QFormLayout.FieldRole, self.end_date)
        self.verticalLayout_2.addWidget(self.groupBox)
        self.verticalLayout = QtWidgets.QVBoxLayout()
        self.verticalLayout.setObjectName("verticalLayout")
        self.debug = QtWidgets.QCheckBox(Startup)
        self.debug.setObjectName("debug")
        self.verticalLayout.addWidget(self.debug)
        self.force = QtWidgets.QCheckBox(Startup)
        self.force.setObjectName("force")
        self.verticalLayout.addWidget(self.force)
        self.localtime = QtWidgets.QCheckBox(Startup)
        self.localtime.setObjectName("localtime")
        self.verticalLayout.addWidget(self.localtime)
        self.ffmpeg = QtWidgets.QCheckBox(Startup)
        self.ffmpeg.setObjectName("ffmpeg")
        self.verticalLayout.addWidget(self.ffmpeg)
        self.forcetranscoding = QtWidgets.QCheckBox(Startup)
        self.forcetranscoding.setObjectName("forcetranscoding")
        self.verticalLayout.addWidget(self.forcetranscoding)
        self.verticalLayout_2.addLayout(self.verticalLayout)
        self.horizontalLayout_3.addLayout(self.verticalLayout_2)
        self.cameras = QtWidgets.QTreeWidget(Startup)
        self.cameras.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.cameras.setRootIsDecorated(True)
        self.cameras.setUniformRowHeights(False)
        self.cameras.setHeaderHidden(False)
        self.cameras.setObjectName("cameras")
        item_0 = QtWidgets.QTreeWidgetItem(self.cameras)
        self.horizontalLayout_3.addWidget(self.cameras)
        self.horizontalLayout_3.setStretch(0, 3)
        self.horizontalLayout_3.setStretch(1, 2)
        self.gridLayout_2.addLayout(self.horizontalLayout_3, 2, 0, 1, 2)
        spacerItem1 = QtWidgets.QSpacerItem(318, 60, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem1, 3, 0, 1, 1)
        self.horizontalLayout = QtWidgets.QHBoxLayout()
        self.horizontalLayout.setObjectName("horizontalLayout")
        self.test_connection_button = QtWidgets.QPushButton(Startup)
        self.test_connection_button.setObjectName("test_connection_button")
        self.horizontalLayout.addWidget(self.test_connection_button)
        self.start_downloading_button = QtWidgets.QPushButton(Startup)
        self.start_downloading_button.setObjectName("start_downloading_button")
        self.horizontalLayout.addWidget(self.start_downloading_button)
        self.gridLayout_2.addLayout(self.horizontalLayout, 3, 1, 1, 1)

        self.retranslateUi(Startup)
        QtCore.QMetaObject.connectSlotsByName(Startup)
        Startup.setTabOrder(self.server_ip, self.username)
        Startup.setTabOrder(self.username, self.password)
        Startup.setTabOrder(self.password, self.downloads_folder)
        Startup.setTabOrder(self.downloads_folder, self.downloads_folder_button)
        Startup.setTabOrder(self.downloads_folder_button, self.folder_behavior)
        Startup.setTabOrder(self.folder_behavior, self.download_type)
        Startup.setTabOrder(self.download_type, self.video_format)
        Startup.setTabOrder(self.video_format, self.start_date)
        Startup.setTabOrder(self.start_date, self.end_date)
        Startup.setTabOrder(self.end_date, self.debug)
        Startup.setTabOrder(self.debug, self.force)
        Startup.setTabOrder(self.force, self.localtime)
        Startup.setTabOrder(self.localtime, self.ffmpeg)
        Startup.setTabOrder(self.ffmpeg, self.forcetranscoding)
        Startup.setTabOrder(self.forcetranscoding, self.cameras)
        Startup.setTabOrder(self.cameras, self.test_connection_button)
        Startup.setTabOrder(self.test_connection_button, self.start_downloading_button)

    def retranslateUi(self, Startup):
        _translate = QtCore.QCoreApplication.translate
        Startup.setWindowTitle(_translate("Startup", "HikLoad - Startup Window"))
        self.label.setText(_translate("Startup", "Server IP"))
        self.label_2.setText(_translate("Startup", "Username"))
        self.label_6.setText(_translate("Startup", "Downloads folder"))
        self.label_3.setText(_translate("Startup", "Password"))
        self.downloads_folder_button.setText(_translate("Startup", "..."))
        self.label_4.setText(_translate("Startup", "Folder behavior"))
        self.folder_behavior.setCurrentText(_translate("Startup", "Do not create any more folders"))
        self.folder_behavior.setItemText(0, _translate("Startup", "Do not create any more folders"))
        self.folder_behavior.setItemText(1, _translate("Startup", "Create one folder per camera"))
        self.folder_behavior.setItemText(2, _translate("Startup", "Create one folder per day"))
        self.folder_behavior.setItemText(3, _translate("Startup", "Create one folder per month"))
        self.folder_behavior.setItemText(4, _translate("Startup", "Create one folder per year"))
        self.label_9.setText(_translate("Startup", "Download Type"))
        self.download_type.setCurrentText(_translate("Startup", "Videos"))
        self.download_type.setItemText(0, _translate("Startup", "Videos"))
        self.download_type.setItemText(1, _translate("Startup", "Photos (EXPERIMENTAL!)"))
        self.label_5.setText(_translate("Startup", "Video format"))
        self.video_format.setCurrentText(_translate("Startup", "mkv"))
        self.video_format.setItemText(0, _translate("Startup", "mkv"))
        self.video_format.setItemText(1, _translate("Startup", "mp4"))
        self.video_format.setItemText(2, _translate("Startup", "avi"))
        self.label_7.setText(_translate("Startup", "Start date"))
        self.label_8.setText(_translate("Startup", "End date"))
        self.debug.setText(_translate("Startup", "Enable debug mode"))
        self.force.setText(_translate("Startup", "Force downloading and saving of files"))
        self.localtime.setText(_translate("Startup", "Save filenames using date in local time instead of UTC"))
        self.ffmpeg.setText(_translate("Startup", "Enable ffmpeg and disable downloading directly from server"))
        self.forcetranscoding.setText(_translate("Startup", "Force transcoding if downloading directly from server"))
        self.cameras.setSortingEnabled(False)
        self.cameras.headerItem().setText(0, _translate("Startup", "Cameras from which to download"))
        __sortingEnabled = self.cameras.isSortingEnabled()
        self.cameras.setSortingEnabled(False)
        self.cameras.topLevelItem(0).setText(0, _translate("Startup", "Test the connection to see the channels"))
        self.cameras.setSortingEnabled(__sortingEnabled)
        self.test_connection_button.setText(_translate("Startup", "Test connection"))
        self.start_downloading_button.setText(_translate("Startup", "Start downloading"))
