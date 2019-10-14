"""
Provides helper functions used throughout the InvenTree project
"""

import io
import re
import json
import os.path
from PIL import Image

from wsgiref.util import FileWrapper
from django.http import StreamingHttpResponse
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext as _


def TestIfImage(img):
    """ Test if an image file is indeed an image """
    try:
        Image.open(img).verify()
        return True
    except:
        return False


def TestIfImageURL(url):
    """ Test if an image URL (or filename) looks like a valid image format.

    Simply tests the extension against a set of allowed values
    """
    return os.path.splitext(os.path.basename(url))[-1].lower() in [
        '.jpg', '.jpeg',
        '.png', '.bmp',
        '.tif', '.tiff',
        '.webp', '.gif',
    ]
        

def str2bool(text, test=True):
    """ Test if a string 'looks' like a boolean value.

    Args:
        text: Input text
        test (default = True): Set which boolean value to look for

    Returns:
        True if the text looks like the selected boolean value
    """
    if test:
        return str(text).lower() in ['1', 'y', 'yes', 't', 'true', 'ok', 'on', ]
    else:
        return str(text).lower() in ['0', 'n', 'no', 'none', 'f', 'false', 'off', ]


def WrapWithQuotes(text, quote='"'):
    """ Wrap the supplied text with quotes

    Args:
        text: Input text to wrap
        quote: Quote character to use for wrapping (default = "")

    Returns:
        Supplied text wrapped in quote char
    """

    if not text.startswith(quote):
        text = quote + text

    if not text.endswith(quote):
        text = text + quote

    return text


def MakeBarcode(object_type, object_id, object_url, data={}):
    """ Generate a string for a barcode. Adds some global InvenTree parameters.

    Args:
        object_type: string describing the object type e.g. 'StockItem'
        object_id: ID (Primary Key) of the object in the database
        object_url: url for JSON API detail view of the object
        data: Python dict object containing extra datawhich will be rendered to string (must only contain stringable values)

    Returns:
        json string of the supplied data plus some other data
    """

    # Add in some generic InvenTree data
    data['type'] = object_type
    data['id'] = object_id
    data['url'] = object_url
    data['tool'] = 'InvenTree'

    return json.dumps(data, sort_keys=True)


def GetExportFormats():
    """ Return a list of allowable file formats for exporting data """
    
    return [
        'csv',
        'tsv',
        'xls',
        'xlsx',
        'json',
        'yaml',
    ]


def DownloadFile(data, filename, content_type='application/text'):
    """ Create a dynamic file for the user to download.
    
    Args:
        data: Raw file data (string or bytes)
        filename: Filename for the file download
        content_type: Content type for the download

    Return:
        A StreamingHttpResponse object wrapping the supplied data
    """

    filename = WrapWithQuotes(filename)

    if type(data) == str:
        wrapper = FileWrapper(io.StringIO(data))
    else:
        wrapper = FileWrapper(io.BytesIO(data))

    response = StreamingHttpResponse(wrapper, content_type=content_type)
    response['Content-Length'] = len(data)
    response['Content-Disposition'] = 'attachment; filename={f}'.format(f=filename)

    return response


def ExtractSerialNumbers(serials, expected_quantity):
    """ Attempt to extract serial numbers from an input string.
    - Serial numbers must be integer values
    - Serial numbers must be positive
    - Serial numbers can be split by whitespace / newline / commma chars
    - Serial numbers can be supplied as an inclusive range using hyphen char e.g. 10-20

    Args:
        expected_quantity: The number of (unique) serial numbers we expect
    """

    serials = serials.strip()

    groups = re.split("[\s,]+", serials)

    numbers = []
    errors = []

    try:
        expected_quantity = int(expected_quantity)
    except ValueError:
        raise ValidationError([_("Invalid quantity provided")])

    if len(serials) == 0:
        raise ValidationError([_("Empty serial number string")])

    for group in groups:

        group = group.strip()

        # Hyphen indicates a range of numbers
        if '-' in group:
            items = group.split('-')

            if len(items) == 2:
                a = items[0].strip()
                b = items[1].strip()

                try:
                    a = int(a)
                    b = int(b)

                    if a < b:
                        for n in range(a, b + 1):
                            if n in numbers:
                                errors.append(_('Duplicate serial: {n}'.format(n=n)))
                            else:
                                numbers.append(n)
                    else:
                        errors.append(_("Invalid group: {g}".format(g=group)))

                except ValueError:
                    errors.append(_("Invalid group: {g}".format(g=group)))
                    continue
            else:
                errors.append(_("Invalid group: {g}".format(g=group)))
                continue

        else:
            try:
                n = int(group)
                if n in numbers:
                    errors.append(_("Duplicate serial: {n}".format(n=n)))
                else:
                    numbers.append(n)
            except ValueError:
                errors.append(_("Invalid group: {g}".format(g=group)))

    if len(errors) > 0:
        raise ValidationError(errors)

    if len(numbers) == 0:
        raise ValidationError([_("No serial numbers found")])

    # The number of extracted serial numbers must match the expected quantity
    if not expected_quantity == len(numbers):
        raise ValidationError([_("Number of unique serial number ({s}) must match quantity ({q})".format(s=len(numbers), q=expected_quantity))])

    return numbers
