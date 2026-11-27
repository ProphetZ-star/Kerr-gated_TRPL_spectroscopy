'''Confirmations before an action that would remove the installed wavelength calibration (and intensity response).

The worker takes the calibration out of use when the spectrometer setting it was measured for changes: a new centre
wavelength or grating, or another spectrometer (it stays saved for its setting and comes back on returning to it). A
measured intensity response is lost with the setting; these checks ask first.'''
from PySide6.QtWidgets import QMessageBox


def installed_calibration(snapshot):
    return bool(((snapshot or {}).get('calibration') or {}).get('wavelengths_nm'))


def installed_response(snapshot, measured_only=True):
    source = ((snapshot or {}).get('response') or {}).get('source')
    return source == 'measured' if measured_only else bool(source)


def confirm_calibration_loss(parent, snapshot, change, measured_response_only=True, keeps_calibration=False, calibration_saved=False):
    '''Ask before `change` (e.g. "Center wavelength 600 → 650 nm") removes the installed calibration. True when
    nothing would be lost or the user goes ahead; Cancel is the default. keeps_calibration: the change keeps the
    wavelength calibration (a slit-only change) and drops only the measured intensity response. calibration_saved:
    the calibration leaves with the setting but is saved for it and comes back on returning (a new centre wavelength
    or grating), so only the measured intensity response is lost.'''
    saved_note = calibration_saved and installed_calibration(snapshot)
    calibration = installed_calibration(snapshot) and not keeps_calibration and not calibration_saved
    response = installed_response(snapshot, measured_response_only)
    if not calibration and not response:
        return True
    lost = ' and '.join(part for part in ('the wavelength calibration' if calibration else '',
                                          'the intensity response' if response else '') if part)
    title = 'Remove the calibration?' if calibration else 'Remove the intensity response?'
    box = QMessageBox(QMessageBox.Icon.Warning, title, f'{change} removes {lost}.',
                      QMessageBox.StandardButton.NoButton, parent)
    box.setInformativeText(
        ('A calibration belongs to the spectrometer setting it was measured with. Until a new one is installed on the '
         'Spectral calibration page, spectra are shown against pixel.' if calibration else
         'A measured response carries the throughput of the setting it was measured with; it would have to be measured again.'
         + (' The wavelength calibration is kept: the slit does not move wavelengths on the CCD.' if keeps_calibration else '')
         + (' The wavelength calibration is saved for the current setting and comes back when you return to it.' if saved_note else ''))
        + '\n\nExperiments already recorded keep the calibration and response stored with them.')
    go = box.addButton('Change and remove', QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel); box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is go
