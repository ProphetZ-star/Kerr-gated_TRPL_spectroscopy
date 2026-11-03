"""CCD readout geometry shared by the hardware and simulated cameras.

Coordinates are 0-based, inclusive chip pixels. The installed wavelength calibration stays defined on
the full 1024-pixel chip; a cropped/binned spectrum takes its axis at the centres of its binned pixels."""
import numpy as np

CHIP_WIDTH=1024
HBINS=(1,2,4,8,16)
RAW_FULL_SCALE=65535


def default_readout(rows):
    return dict(mode='fvb',x0=0,x1=CHIP_WIDTH-1,y0=0,y1=rows-1,hbin=1,vbin='full')


def normalize_readout(data,rows):
    """Return (readout, notes). Out-of-range or indivisible values are trimmed, and each change is reported."""
    notes=[]
    mode=data.get('mode','fvb')
    if mode not in ('fvb','roi'):raise ValueError('Readout mode must be FVB or ROI')
    hbin=int(data.get('hbin',1))
    if hbin not in HBINS:raise ValueError('Horizontal binning must be one of '+', '.join(map(str,HBINS)))
    x0=int(data.get('x0',0));x1=int(data.get('x1',CHIP_WIDTH-1))
    if not 0<=x0<x1<=CHIP_WIDTH-1:raise ValueError(f'ROI X must satisfy 0 ≤ start < end ≤ {CHIP_WIDTH-1}')
    width=x1-x0+1
    if width<hbin:raise ValueError('ROI X is narrower than the horizontal binning')
    if width%hbin:
        x1-=width%hbin;notes.append(f'ROI X end trimmed to {x1} so the width is a multiple of H bin {hbin}')
    if mode=='fvb':
        y0,y1,vbin=0,rows-1,'full'
    else:
        y0=int(data.get('y0',0));y1=int(data.get('y1',rows-1))
        if not 0<=y0<=y1<=rows-1:raise ValueError(f'ROI Y must satisfy 0 ≤ start ≤ end ≤ {rows-1}')
        vbin=data.get('vbin','full')
        if vbin!='full':
            vbin=int(vbin)
            if vbin<1:raise ValueError('Vertical binning must be Full or a positive whole number')
            height=y1-y0+1
            if vbin>=height:vbin='full'
            elif height%vbin:
                y1-=height%vbin;notes.append(f'ROI Y end trimmed to {y1} so the height is a multiple of V bin {vbin}')
    return dict(mode=mode,x0=x0,x1=x1,y0=y0,y1=y1,hbin=hbin,vbin=vbin),notes


def readout_shape(readout):
    """(rows read from the chip, spectrum pixels)."""
    height=readout['y1']-readout['y0']+1
    rows=1 if readout['vbin']=='full' else height//readout['vbin']
    return rows,(readout['x1']-readout['x0']+1)//readout['hbin']


def spectrum_full_scale(readout):
    # Each read (binned) pixel saturates at 16 bits; rows read separately are summed in software.
    return RAW_FULL_SCALE*readout_shape(readout)[0]


def pixel_centers(readout):
    _,pixels=readout_shape(readout)
    return readout['x0']+readout['hbin']*np.arange(pixels)+(readout['hbin']-1)/2


def readout_axis(chip_wavelengths,readout):
    if chip_wavelengths is None:return None
    chip=np.asarray(chip_wavelengths,dtype=float)
    return np.interp(pixel_centers(readout),np.arange(len(chip)),chip).tolist()


def bin_full_spectrum(spectrum,readout,rows):
    """Apply ROI and binning to a full-chip FVB spectrum (simulation only)."""
    _,pixels=readout_shape(readout)
    cropped=np.asarray(spectrum,dtype=np.int64)[readout['x0']:readout['x0']+pixels*readout['hbin']]
    binned=cropped.reshape(pixels,readout['hbin']).sum(axis=1)
    fraction=(readout['y1']-readout['y0']+1)/rows
    return np.rint(binned*fraction).astype(np.int64)


def describe(readout):
    rows,pixels=readout_shape(readout)
    vbin='full' if readout['vbin']=='full' else readout['vbin']
    return (f"{'FVB' if readout['mode']=='fvb' else 'ROI'} · H bin {readout['hbin']} · V bin {vbin}",
            f"X {readout['x0']}–{readout['x1']} · Y {readout['y0']}–{readout['y1']} → {pixels} px" + (f' × {rows} rows summed' if rows>1 else ''))
