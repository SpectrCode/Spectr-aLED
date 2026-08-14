# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


# ============================================================
# WINDOWS VERSION INFO
# ============================================================

version_info = {
    'FixedFileInfo': {
        'filevers': (1, 0, 2, 0),
        'prodvers': (1, 0, 2, 0),
        'mask': 0x3F,
        'flags': 0x0,
        'OS': 0x40004,
        'filetype': 0x1,
        'subtype': 0x0,
        'date': (0, 0)
    },
    'StringFileInfo': {
        'CompanyName': '',
        'FileDescription': 'LED Control Application',
        'FileVersion': '1.0.2.0',
        'InternalName': 'Spectr_aLED',
        'LegalCopyright': '',
        'OriginalFilename': 'Spectr_aLED.exe',
        'ProductName': 'Spectr_aLED',
        'ProductVersion': '1.0.2.0',
    },
    'VarFileInfo': {
        'Translation': (1033, 1200)
    }
}


# ============================================================
# ANALYSIS
# ============================================================

a = Analysis(
    ['..\\script\\main.py'],

    pathex=[
        '..\\script'
    ],

    binaries=[],

    datas=[
        ('..\\script', 'script'),
        ('..\\img', 'img'),
        ('..\\dll', 'dll'),
        ('..\\requirements', 'requirements'),
    ],

    hiddenimports=[
        'numpy',
        'numpy._core',

        'PIL',
        'PIL.Image',
        'PIL.ImageTk',

        'ctypes',

        'tkinter',
        'tkinter.ttk',

        'json',
        'threading',
        'socket',
        'struct',

        'urllib',
        'urllib.request',

        'queue',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],

    excludes=[
        'pytest',
        'pydoc',
        'doctest',
        'lib2to3',
        'Tkinter',
    ],

    noarchive=True,

    optimize=0,
)


# ============================================================
# PYZ
# ============================================================

pyz = PYZ(
    a.pure
)


# ============================================================
# EXE
# ============================================================

exe = EXE(
    pyz,
    a.scripts,

    exclude_binaries=True,

    name='Spectr_aLED',

    debug=False,

    bootloader_ignore_signals=False,

    strip=False,

    upx=False,

    upx_exclude=[],

    runtime_tmpdir=None,

    console=False,

    disable_windowed_traceback=False,

    argv_emulation=False,

    target_arch=None,

    codesign_identity=None,

    entitlements_file=None,

    icon='..\\img\\ico.ico',

    version_info=version_info,

    embed_manifest=True,
)


# ============================================================
# COLLECT
# ============================================================

coll = COLLECT(
    exe,

    a.binaries,
    a.datas,

    name='Spectr_aLed',

    debug=False,

    strip=False,

    upx=False,

    upx_exclude=[],
)