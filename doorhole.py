#!/usr/bin/env python

# Checks functional requirements with regexes
# Builds functional matrix

import doorstop
from doorstop.core.types import iter_documents, iter_items, Level
import os
import sys
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWebEngineWidgets import *
import logging
import markdown
from plantuml_markdown import PlantUMLMarkdownExtension
import tempfile
import copy

EXTENSIONS = (
	'markdown.extensions.extra',
	'markdown.extensions.sane_lists',
	PlantUMLMarkdownExtension(
		server='http://www.plantuml.com/plantuml',
		cachedir=tempfile.gettempdir(),
		format='svg',
		classes='class1,class2',
		title='UML',
		alt='UML Diagram',
	),
)

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logging.getLogger('doorstop').setLevel(logging.WARNING)
logging.getLogger('MARKDOWN').setLevel(logging.WARNING)
logger = logging.getLogger
log = logger(__name__)


# requirements tree is a global because it's shared by all classes.
# Maybe it should become a singleton.
reqtree = None

def build_child_links_index():
	"""Scan the entire requirements tree and build a mapping from a UID (str)
	to the list of UIDs (str) of items that declare it as a parent link."""
	global reqtree
	index = {}
	for document in reqtree:
		for item in iter_items(document):
			child_uid = str(item.uid).strip()
			for link in (item.links or []):
				parent_uid = str(link).strip()
				index.setdefault(parent_uid, []).append(child_uid)
	return index

class LinksDelegate(QStyledItemDelegate):
	"""Custom delegate for rendering clickable links in the 'links' column."""

	LINK_COLUMN_NAMES = ('links', 'childlinks')

	def __init__(self, parent=None):
		super(LinksDelegate, self).__init__(parent)
		self.doc = QTextDocument(self)
		self._currentCacheKey = None

	def getDoc(self, option, index, linksList):
		width = option.rect.width()
		cache_key = (index.row(), tuple(linksList), width)
		if self._currentCacheKey == cache_key:
			return
		self._currentCacheKey = cache_key
		html = '<html><body style="margin: 4px;">'
		for uid in linksList:
			if uid:
				html += f'<a href="link:{uid}" style="color: blue; text-decoration: underline;">{uid}</a><br/>'
		html += '</body></html>'
		self.doc.setHtml(html)
		self.doc.setTextWidth(width)
		
	def paint(self, painter, option, index):
		mdl = index.model()
		if mdl._headerData[index.column()] in self.LINK_COLUMN_NAMES:
			links_data = mdl._data[index.row()][index.column()]
			linksList = self.parseLinks(links_data)
			self.getDoc(option, index, linksList)
			ctx = QAbstractTextDocumentLayout.PaintContext()
			painter.save()
			painter.translate(option.rect.topLeft())
			painter.setClipRect(option.rect.translated(-option.rect.topLeft()))
			self.doc.documentLayout().draw(painter, ctx)
			painter.restore()
		else:
			super(LinksDelegate, self).paint(painter, option, index)

	def sizeHint(self, option, index):
		mdl = index.model()
		if mdl._headerData[index.column()] in self.LINK_COLUMN_NAMES:
			links_data = mdl._data[index.row()][index.column()]
			linksList = self.parseLinks(links_data)
			self.getDoc(option, index, linksList)
			return QSize(self.doc.idealWidth(), self.doc.size().height())
		return super(LinksDelegate, self).sizeHint(option, index)

	def parseLinks(self, links_data):
		"""Parse links data into a list of UIDs.
		
		Doorstop links format: UID(<uid>
		stamp=<stamp>)
		Example: UID(SysR-002
		stamp=mcidGMWQFubYs9jDkGkCaoHchg3-41N0HMTToh_AiBl=)
		Multiple links are separated by newlines or commas.
		"""
		if not links_data or links_data == 'None' or links_data == '[]':
			return []

		if 'UID(' not in links_data:
			# Einfaches Format: Komma-getrennte UIDs (z.B. berechnete Child-Links)
			raw = links_data.strip('[]').replace("'", '')
			uids = [u.strip().rstrip(',') for u in raw.replace('\n', ',').split(',')]
			return [u for u in uids if u]

		# Remove list brackets if present
		links_data = links_data.strip('[]').replace("'", '')
		
		uids = []
		# Find all occurrences of "UID(" and extract the UID
		idx = 0
		while True:
			pos = links_data.find('UID(', idx)
			if pos == -1:
				break
			# Find the closing parenthesis
			end_pos = links_data.find(')', pos)
			if end_pos == -1:
				break
			# Extract content between "UID(" and ")"
			content = links_data[pos+4:end_pos]
			# Extract UID (everything before " stamp=" or newline)
			stamp_pos = content.find(' stamp=')
			newline_pos = content.find('\n')
			if stamp_pos != -1:
				uid = content[:stamp_pos].strip().rstrip(',')
			elif newline_pos != -1:
				uid = content[:newline_pos].strip().rstrip(',')
			else:
				uid = content.strip().rstrip(',')
			if uid and uid not in uids:
				uids.append(uid)
			idx = end_pos + 1
		
		return uids

	def getLinkAtPos(self, pos):
		"""Get the link URL at the given position, if any."""
		anchor = self.doc.documentLayout().anchorAt(pos)
		if anchor.startswith('link:'):
			return anchor[5:]  # Remove 'link:' prefix
		return None

class RequirementsDelegate(QStyledItemDelegate):
	def __init__(self, parent=None):
		super(RequirementsDelegate, self).__init__(parent)
		self.doc = QTextDocument(self)
		self.md = markdown.Markdown(extensions=EXTENSIONS)
		self._htmlCache = {}  # key: (uid, text_hash, width) -> html string
		self._currentCacheKey = None

	def createEditor(self, parent, option, index):
		if index.model()._headerData[index.column()] == 'text':
			edit = QPlainTextEdit(parent)
			# set fixed font
			fixed_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
			fixed_font.setStyleHint(QFont.TypeWriter)
			edit.setFont(fixed_font)
			# set colors
			edit.setStyleSheet("""
			QPlainTextEdit {
			color: black;
			background: white;
			}
			""")
			return edit
		return super(RequirementsDelegate, self).createEditor(parent, option, index) # editor chosen with the QtEditRole in model.data()

	def setEditorData(self, editor, index):
		if index.model()._headerData[index.column()] == 'text':
			editor.insertPlainText(index.data())
		if index.model()._headerData[index.column()] == 'level': # would create an empty QLineEdit otherwise
			editor.setText(index.data())
		return super(RequirementsDelegate, self).setEditorData(editor, index)

	def setModelData(self, editor, model, index): # called after closing the editor
		# We need to extract a value. Possible editors: https://doc.qt.io/qtforpython/PySide2/QtWidgets/QItemEditorFactory.html

		# There ought to be be a better way.
		editorType = str(type(editor))
		if 'QComboBox' in editorType:
			model.setData(index, editor.currentText())
		elif 'QLineEdit' in editorType:
			model.setData(index, editor.text())
		elif 'QPlainTextEdit' in editorType:
			model.setData(index, editor.toPlainText())

	def getDoc(self, option, index):
		mdl = index.model()
		if mdl._headerData[index.column()] != 'text':
			return
		item = mdl._data[index.row()][len(mdl._headerData)]
		width = option.rect.width()
		cache_key = (str(item.uid), hash(item.get('text')), width)

		if self._currentCacheKey == cache_key:
			return  # Doc ist schon exakt das, was wir brauchen

		if cache_key in self._htmlCache:
			cached = self._htmlCache[cache_key]
		else:
			text = item.get('text')
			level = str(item.get('level'))
			header = str(item.get('header'))
			item_path = item.get('path')
			item_path = os.path.dirname(os.path.realpath(item_path))

			# mimick DS title and header attributes
			lines = [l for l in text.splitlines()]
			heading = ''
			if level.endswith('.0'):  # Chapter title
				heading += '#'*level.count('.') + ' ' + level[:-2] + ' '
				if header.strip():
					heading += header.strip() + '\n\n'
					if len(lines):
						lines = [heading] + lines
					else:
						lines = [heading]
				else:
					if len(lines):
						heading += lines[0] + '\n\n'
						lines = [heading] + lines[1:]
					else:
						lines = [heading]
			else:  # Requirement
				if header.strip():
					heading += '#'*(level.count('.') +1) + ' ' + level + ' ' + header.strip()
					if item.normative:
						heading += ' (' + str(item.uid) + ')'
				else:
					heading += '#'*(level.count('.') +1) + ' ' + level + ' ' + str(item.uid)
				lines = [heading] + lines
			text = '\n'.join(lines)

			cwd_bkp = os.getcwd()
			try:
				os.chdir(item_path)
				html = self.md.convert(text)
				cached = ('html', html)
			except Exception as e:
				warning = '**An error occurred while displaying the content**\n\n: ' + str(e) + '\n\n'
				text = warning + text
				cached = ('md', text)
			finally:
				os.chdir(cwd_bkp)

			self._htmlCache[cache_key] = cached

		self._currentCacheKey = cache_key
		kind, content = cached
		if kind == 'html':
			self.doc.setHtml(content)
		else:
			self.doc.setMarkdown(content)
		self.doc.setTextWidth(width)
		
	def paint(self, painter, option, index):
		mdl = index.model()
		if mdl._headerData[index.column()] == 'text':
			# get rich text document and paint it
			self.getDoc(option, index)
			ctx = QAbstractTextDocumentLayout.PaintContext()
			painter.save()
			painter.translate(option.rect.topLeft());
			painter.setClipRect(option.rect.translated(-option.rect.topLeft()))
			self.doc.documentLayout().draw(painter, ctx)
			painter.restore()
		else:
			super(RequirementsDelegate, self).paint(painter, option, index)

	def sizeHint(self, option, index):
		mdl = index.model()
		if mdl._headerData[index.column()] == 'text':
			# get rich text document and size it
			self.getDoc(option, index)
			#log.debug(mdl._headerData[index.column()] + "\t W: " + str(self.doc.idealWidth()) + " H: " +  str(self.doc.size().height()))
			return QSize(self.doc.idealWidth(), self.doc.size().height())
		else:
			return QSize(0,0)
			#super(RequirementsDelegate, self).sizeHint(option, index)

class RequirementSetModel(QAbstractTableModel):
	# Standard-Spalten, die immer vorne stehen (fest definiert statt Magic Numbers)
	_STANDARD_LEADING = ['uid', 'path', 'root', 'normative', 'derived', 'reviewed',
							'level', 'header', 'ref', 'references', 'links', 'childlinks']

	_DISPLAY_NAMES = {
		'links': 'Parent Links',
		'childlinks': 'Child Links',
	}

	def __init__(self, docId=None, parent=None):
		super(RequirementSetModel, self).__init__(parent)
		self._docId = docId
		self.load()

	@Slot()
	def load(self):
		global reqtree
		self._document = reqtree.find_document(self._docId)

		stdHeaderData = {'path', 'root', 'active', 'normative', 'uid', 'level',
							'header', 'text', 'derived', 'ref', 'references', 'reviewed', 'links'}

		headerData = []
		for item in iter_items(self._document):
			headerData += list(item._data.keys())
			headerData = list(set(headerData))

		userHeaderData = set(headerData) - stdHeaderData
		if userHeaderData:
			log.debug('['+str(self._document)+'] Custom requirements attributes: ' + str(userHeaderData))

		self._headerData = self._STANDARD_LEADING + list(userHeaderData) + ['text']

		# Child-Links müssen einmalig über den GESAMTEN Tree berechnet werden,
		# da Kinder auch in anderen Dokumenten liegen können.
		childLinksIndex = build_child_links_index()

		self._data = []
		for item in iter_items(self._document):
			item_uid = str(item.uid).strip()
			row = []
			for f in self._headerData:
				if f == 'childlinks':
					row.append(','.join(childLinksIndex.get(item_uid, [])))
				else:
					row.append(str(item.get(f)))
			row.append(item)
			self._data.append(row)
		log.debug('['+str(self._document)+'] Requirements reloaded')
		
	# TableView methods that must be implemented
	def rowCount(self, index=QModelIndex()):
		return len(self._data)

	def columnCount(self, index=QModelIndex()):
		return len(self._headerData)

	def data(self, index, role=Qt.DisplayRole):
		if not index.isValid():
			return None

		item = self._data[index.row()][len(self._headerData)]
		colName = self._headerData[index.column()]

		if role == Qt.DisplayRole:
			if colName == 'childlinks':
				return self._data[index.row()][index.column()]
			return str(item.get(colName))

		if role == Qt.EditRole:
			if colName == 'childlinks':
				return self._data[index.row()][index.column()]
			return item.get(colName)

		if role == Qt.BackgroundRole:
			if not item.get('normative') or str(item.get('level')).endswith('.0'):
				return QBrush(QColor('lightGray'))

		if role == Qt.ForegroundRole:
			if not item.get('normative') or str(item.get('level')).endswith('.0'):
				return QBrush(QColor('gray'))

		if colName == 'links' and role == Qt.UserRole:
			return item.get(colName)
		
	def headerData(self, num, orientation, role=Qt.DisplayRole):
		if orientation == Qt.Horizontal:
			if role == Qt.DisplayRole:
				key = self._headerData[num]
				return self._DISPLAY_NAMES.get(key, key)
			if role == Qt.ForegroundRole:
				leading = len(self._STANDARD_LEADING)
				if leading <= num < len(self._headerData) - 1:
					return QBrush(QColor('blue'))

		if orientation == Qt.Vertical:
			item = self._data[num][len(self._headerData)]
			if role == Qt.DisplayRole:
				return str(item.get('uid'))
			if role == Qt.ForegroundRole:
				if not item.get('reviewed'):
					return QBrush(QColor('orange'))
				if not item.get('normative') or str(item.get('level')).endswith('.0'):
					return QBrush(QColor('gray'))
				return QBrush(QColor('darkGreen'))
			if role == Qt.ToolTipRole:
				return "Reviewed: " + str(item.get('reviewed'))
		return QAbstractTableModel.headerData(self, num, orientation, role)

	def flags(self, index):
		colName = self._headerData[index.column()]
		base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
		if colName not in ('links', 'childlinks'):
			base |= Qt.ItemIsEditable
		return base

	def setData(self, index, text):
		item = self._data[index.row()][len(self._headerData)]
		attr = self._headerData[index.column()]

		# Boolean values are passed as "True" or "False" strings, so we need to determine whether the original datatype was boolean.
		if type(item.get(attr)) == bool:
			if text == 'True':
				text = True
			else:
				text = False

		# Integer values are passed as strings, so we need to convert back to integer
		if type(item.get(attr)) == int:
			text = int(text)

		# Strings are left as they are
		if item.get(attr) != text:
			try:
				attributes = { attr : text }
				item.set_attributes(attributes)
				item.save()
				self._data[index.row()][index.column()] = item.get(attr)
				log.debug('Updated requirement [' + str(item.get('uid')) + '] attribute ['+attr+']')
			except doorstop.DoorstopError:
				log.error('Requirement [' + str(item.get('uid')) + '] file not saved - manual edit required: ' + path)
		self.layoutChanged.emit()

	def newReq(self, level=None):
		global reqtree
		if level is not None:
			item = reqtree.add_item(value=str(self._docId), level=level)
			log.debug("["+str(self._docId)+"] Added requirement " + str(item))
			if item.get('level').heading: # make title items non-normative by default
				item.set('normative', False)
			item.set('derived', False) # set 'derived' property to False by default
			self.load() # reload the whole document
			self.layoutChanged.emit()

	def delReq(self, row):
		global reqtree
		item = self._data[row][len(self._headerData)]
		reqid = str(item)
		item.delete() # doorstop item deletion
		log.debug("["+str(self._docId)+"] Deleted requirement " + reqid)
		self.load() # reload the whole document
		self.layoutChanged.emit()

	def insertRowBefore(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			item = self._data[row][len(self._headerData)]
			new_level = Level(item.get('level')) # just use the level of the clicked req
			self.newReq(new_level)

	def insertRowAfter(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			item = self._data[row][len(self._headerData)]
			new_level = self._getSubsequentLevel(item.get('level'))
			self.newReq(new_level)

	def _getSubsequentLevel(self, level):
		new_level = Level(level) # create a new object, the '=' operator does a reference
		if new_level.heading:
			parts = str(new_level).split('.') # adding +1 to level doesn't work as expected when the level is a heading
			parts[-1] = 1 # x.x.x.0 --> x.x.x.1
			new_level = Level(parts)
		else:
			new_level += 1
		return new_level

	def deactivateRow(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			item = self._data[row][len(self._headerData)]
			item.set('normative', False)

	def activateRow(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			item = self._data[row][len(self._headerData)]
			item.set('normative', True)

	def deriveRow(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			item = self._data[row][len(self._headerData)]
			item.set('derived', True)

	def underiveRow(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			item = self._data[row][len(self._headerData)]
			item.set('derived', False)

	def deleteRow(self, qidx):
		row = qidx.row()
		if row < len(self._data): # clicked requirement actually exists
			qm = QMessageBox()
			qm.setText(str("This will delete the requirement from disk.\nYou will not be able to recover it unless it's versioned.\n\nAre you absolutely sure?"))
			qm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
			ret = qm.exec()
			if ret == QMessageBox.Yes:
				self.delReq(row)

	def getItem(self, qidx):
		row = qidx.row()
		if row < len(self._data):
			return self._data[row][len(self._headerData)]
		else:
			return None

class RequirementManager(QWidget):
	'''
	Requirement document viewer with editor.
	The display format is a table.
	The model uses doorstop as source.

	Allows:
		- view requirement
		- edit requirement
		- add requirement
		- delete requirement
		- reload requirements

	Uses:
		- doorstop as backend
		- table view
	'''

	def __init__(self, docId=None, parent=None):
		super(RequirementManager, self).__init__(parent)
		self._docId = docId
		self._geometryInitialized = False
		self.load()

	def showEvent(self, event):
		super().showEvent(event)
		if not self._geometryInitialized:
			self._geometryInitialized = True
			# Jetzt hat das Widget garantiert echte Geometrie
			self.view.resizeColumnsToContents()
			self.view.resizeRowsToContents()
			
	def load(self):
		self.loadModel() # fills in the table
		self.loadDelegate() # delegate is necessary to edit the "text" field
		self.loadView() # table view

	def loadModel(self):
		self.model = RequirementSetModel(self._docId)

	def loadDelegate(self):
		self.delegate = RequirementsDelegate()
		self.linksDelegate = LinksDelegate()

	def onReload(self):
		self.model.load()
		self.delegate._htmlCache.clear()
		self.delegate._currentCacheKey = None
		self.model.layoutChanged.emit()

	def loadView(self):
		# Table
		self.view = QTableView()
		self.view.setModel(self.model)
		self.view.setItemDelegate(self.delegate)
		# Set custom delegate for links column
		linksCol = self.model._headerData.index('links')
		childLinksCol = self.model._headerData.index('childlinks')
		self.view.setItemDelegateForColumn(linksCol, self.linksDelegate)
		self.view.setItemDelegateForColumn(childLinksCol, self.linksDelegate)
		# Connect link click handler
		self.view.clicked.connect(self.onLinkClicked)
		self.view.setContextMenuPolicy(Qt.CustomContextMenu)
		self.view.customContextMenuRequested.connect(self.onCustomContextMenuRequested)

		# Table appearance
		self.view.setMinimumSize(1024, 768)
		self.view.hideColumn(self.model._headerData.index('path'))
		self.view.hideColumn(self.model._headerData.index('root'))
		self.view.hideColumn(self.model._headerData.index('uid'))
		self.view.hideColumn(self.model._headerData.index('ref'))
		self.view.hideColumn(self.model._headerData.index('references'))

		self.view.horizontalHeader().setStretchLastSection(True)
		self.view.setWordWrap(True)
		self.view.resizeColumnsToContents()
		self.view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
		self.view.setSelectionMode(QAbstractItemView.SingleSelection)
		self.view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
		self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel) # only has effect on the scrollbar dragging
		self.view.verticalScrollBar().setSingleStep(15) # mouse wheel scrolling: restricted to 15px per "click"

		# Buttons
		reloadBtn = QPushButton("Reload")
		reloadBtn.clicked.connect(self.model.load)
		# Placement

		ly = QVBoxLayout()
		lyBtns = QHBoxLayout()
		lyBtns.addWidget(reloadBtn)
		lyBtns.addStretch()
		ly.addLayout(lyBtns)
		ly.addWidget(self.view)
		self.setLayout(ly)

	def onCustomContextMenuRequested(self, pos):
		menu = QMenu()
		idx = self.view.indexAt(pos)
		item = self.model.getItem(idx)
		if item is not None:
			addReqBefore = QAction('Add new requirement before '+str(item))
			addReqBefore.triggered.connect(lambda: self.model.insertRowBefore(idx))
			menu.addAction(addReqBefore)

			addReqAfter = QAction('Add new requirement after '+str(item))
			addReqAfter.triggered.connect(lambda: self.model.insertRowAfter(idx))
			menu.addAction(addReqAfter)
			menu.addSeparator()

			if item.get('level').heading == False:
				if item.get('normative'):
					deactivateReq = QAction('Make '+str(item)+' not normative')
					deactivateReq.triggered.connect(lambda: self.model.deactivateRow(idx))
					deactivateReq.setToolTip("Changes the 'normative' attribute to False.\nNon-normative requirements are informative or are not valid on this specific project.")
					menu.addAction(deactivateReq)
				else:
					activateReq = QAction('Make '+str(item)+' normative')
					activateReq.triggered.connect(lambda: self.model.activateRow(idx))
					activateReq.setToolTip("Changes the 'normative' attribute to True.\nNormative requirements must be implemented.")
					menu.addAction(activateReq)

			if item.get('normative'):
				if item.get('derived'):
					setNotDerived = QAction('Make '+str(item)+' not derived')
					setNotDerived.setToolTip("Changes the 'derived' attribute to False.\nNot derived requirements must have a parent requirement unless they're the top-level requirements.")
					setNotDerived.triggered.connect(lambda: self.model.underiveRow(idx))
					menu.addAction(setNotDerived)
				else:
					setDerived = QAction('Make '+str(item)+' derived')
					setDerived.setToolTip("Changes the 'derived' attribute to True.\nDerived requirements don't need to have a parent requirement even if they're not top-level requirements.")
					setDerived.triggered.connect(lambda: self.model.deriveRow(idx))
					menu.addAction(setDerived)

		menu.addSeparator()
		deleteReq = QAction('Delete '+str(item)+' from disk')
		deleteReq.triggered.connect(lambda: self.model.deleteRow(idx))
		menu.addAction(deleteReq)

		menu.exec(self.view.mapToGlobal(pos))

	def onLinkClicked(self, index):
		colName = self.model._headerData[index.column()]
		if colName not in ('links', 'childlinks'):
			return
		pos = self.view.viewport().mapFromGlobal(QCursor.pos())
		linkPos = self.view.visualRect(index).topLeft()
		relativePos = pos - linkPos
		delegate = self.view.itemDelegateForColumn(index.column())
		if delegate and hasattr(delegate, 'getLinkAtPos'):
			uid = delegate.getLinkAtPos(relativePos)
			if uid:
				self.navigateToRequirement(uid)
				
	def navigateToRequirement(self, uid):
		"""Navigate to the tab and row containing the given UID."""
		global reqtree
		uid = uid.strip().rstrip(',')
		main_window = self.parent()
		while main_window and not isinstance(main_window, MainWindow):
			main_window = main_window.parent()
		if not main_window:
			QMessageBox.critical(self, "Error", "Could not find main window.")
			return

		for document in reqtree:
			for item in iter_items(document):
				if str(item.uid).strip() != uid:
					continue
				for i in range(main_window.tabs.count()):
					tab_widget = main_window.tabs.widget(i)
					req_manager = tab_widget.findChild(RequirementManager)
					if not req_manager or req_manager._docId != document.prefix:
						continue
					model = req_manager.model
					view = req_manager.view
					for row in range(model.rowCount(QModelIndex())):
						row_item = model._data[row][len(model._headerData)]
						if str(row_item.uid).strip() != uid:
							continue
						log.debug(f"Navigating to {uid}: tab {i}, row {row}")
						self._scrollRow = row
						self._scrollModel = model
						self._scrollView = view
						self._scrollAttempts = 0
						main_window.tabs.setCurrentIndex(i)
						QTimer.singleShot(0, self._doScroll)
						return
		log.error(f"UID '{uid}' not found in requirements tree")
		QMessageBox.information(self, "Link Not Found",
								f"Requirement '{uid}' was not found in the requirements tree.")

	def _firstVisibleColumn(self, view, model):
		for c in range(model.columnCount(QModelIndex())):
			if not view.isColumnHidden(c):
				return c
		return 0

	def _doScroll(self):
		if not hasattr(self, '_scrollRow'):
			return
		row, model, view = self._scrollRow, self._scrollModel, self._scrollView

		col = self._firstVisibleColumn(view, model)
		idx = model.index(row, col)
		rect = view.visualRect(idx)

		if (not rect.isValid() or rect.height() == 0) and self._scrollAttempts < 10:
			self._scrollAttempts += 1
			QTimer.singleShot(10, self._doScroll)
			return

		del self._scrollRow, self._scrollModel, self._scrollView, self._scrollAttempts

		view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
		view.setCurrentIndex(idx)
		view.selectRow(row)
		view.setFocus()
		log.debug(f"Scrolled to row {row}")

# Main application
class MainWindow(QMainWindow):
	def __init__(self, parent=None):
		super(MainWindow, self).__init__(parent)
		self.setWindowTitle('Doorhole - doorstop requirements editor')

		global reqtree
		reqtree = doorstop.build()

		self.tabs = QTabWidget()
		self.setCentralWidget(self.tabs)

		# One tab for each document
		for document in reqtree:
			# container widget
			container = QTabWidget()

			# widgets
			reqsW = QWidget()
			reqsView = RequirementManager(document.prefix)

			reqsLy = QVBoxLayout()
			reqsLy.addWidget(reqsView)
			reqsW.setLayout(reqsLy)

			container.addTab(reqsW, 'Requirements')

			title = document.parent + ' -> ' + document.prefix if document.parent else document.prefix
			self.tabs.addTab(container, title)

def start_app():
	app = QApplication(sys.argv)
	win = MainWindow()
	win.show()
	sys.exit(app.exec())


if __name__ == "__main__":
	start_app()
