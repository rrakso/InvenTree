from django.test import TestCase
import django.core.exceptions as django_exceptions
from django.core.exceptions import ValidationError

from .validators import validate_overage, validate_part_name
from . import helpers

from mptt.exceptions import InvalidMove

from stock.models import StockLocation


class ValidatorTest(TestCase):

    """ Simple tests for custom field validators """

    def test_part_name(self):
        """ Test part name validator """

        validate_part_name('hello world')

        with self.assertRaises(django_exceptions.ValidationError):
            validate_part_name('This | name is not } valid')

    def test_overage(self):
        """ Test overage validator """

        validate_overage("100%")
        validate_overage("10")
        validate_overage("45.2 %")

        with self.assertRaises(django_exceptions.ValidationError):
            validate_overage("-1")

        with self.assertRaises(django_exceptions.ValidationError):
            validate_overage("-2.04 %")

        with self.assertRaises(django_exceptions.ValidationError):
            validate_overage("105%")

        with self.assertRaises(django_exceptions.ValidationError):
            validate_overage("xxx %")

        with self.assertRaises(django_exceptions.ValidationError):
            validate_overage("aaaa")


class TestHelpers(TestCase):
    """ Tests for InvenTree helper functions """

    def test_image_url(self):
        """ Test if a filename looks like an image """

        for name in ['ape.png', 'bat.GiF', 'apple.WeBP', 'BiTMap.Bmp']:
            self.assertTrue(helpers.TestIfImageURL(name))

        for name in ['no.doc', 'nah.pdf', 'whatpng']:
            self.assertFalse(helpers.TestIfImageURL(name))

    def test_str2bool(self):
        """ Test string to boolean conversion """

        for s in ['yes', 'Y', 'ok', '1', 'OK', 'Ok', 'tRuE', 'oN']:
            self.assertTrue(helpers.str2bool(s))
            self.assertFalse(helpers.str2bool(s, test=False))

        for s in ['nO', '0', 'none', 'noNE', None, False, 'falSe', 'off']:
            self.assertFalse(helpers.str2bool(s))
            self.assertTrue(helpers.str2bool(s, test=False))

        for s in ['wombat', '', 'xxxx']:
            self.assertFalse(helpers.str2bool(s))
            self.assertFalse(helpers.str2bool(s, test=False))


class TestQuoteWrap(TestCase):
    """ Tests for string wrapping """

    def test_single(self):

        self.assertEqual(helpers.WrapWithQuotes('hello'), '"hello"')
        self.assertEqual(helpers.WrapWithQuotes('hello"'), '"hello"')


class TestMakeBarcode(TestCase):
    """ Tests for barcode string creation """

    def test_barcode(self):

        data = {
            'animal': 'cat',
            'legs': 3,
            'noise': 'purr'
        }

        bc = helpers.MakeBarcode("part", 3, "www.google.com", data)

        self.assertIn('animal', bc)
        self.assertIn('tool', bc)
        self.assertIn('"tool": "InvenTree"', bc)


class TestDownloadFile(TestCase):

    def test_download(self):
        helpers.DownloadFile("hello world", "out.txt")
        helpers.DownloadFile(bytes("hello world".encode("utf8")), "out.bin")


class TestMPTT(TestCase):
    """ Tests for the MPTT tree models """

    fixtures = [
        'location',
    ]

    def setUp(self):
        super().setUp()

        StockLocation.objects.rebuild()

    def test_self_as_parent(self):
        """ Test that we cannot set self as parent """

        loc = StockLocation.objects.get(pk=4)
        loc.parent = loc

        with self.assertRaises(InvalidMove):
            loc.save()

    def test_child_as_parent(self):
        """ Test that we cannot set a child as parent """

        parent = StockLocation.objects.get(pk=4)
        child = StockLocation.objects.get(pk=5)

        parent.parent = child
        
        with self.assertRaises(InvalidMove):
            parent.save()

    def test_move(self):
        """ Move an item to a different tree """

        drawer = StockLocation.objects.get(name='Drawer_1')

        # Record the tree ID
        tree = drawer.tree_id

        home = StockLocation.objects.get(name='Home')

        drawer.parent = home
        drawer.save()

        self.assertNotEqual(tree, drawer.tree_id)
        

class TestSerialNumberExtraction(TestCase):
    """ Tests for serial number extraction code """

    def test_simple(self):

        e = helpers.ExtractSerialNumbers

        sn = e("1-5", 5)
        self.assertEqual(len(sn), 5)
        for i in range(1, 6):
            self.assertIn(i, sn)

        sn = e("1, 2, 3, 4, 5", 5)
        self.assertEqual(len(sn), 5)

        sn = e("1-5, 10-15", 11)
        self.assertIn(3, sn)
        self.assertIn(13, sn)

    def test_failures(self):

        e = helpers.ExtractSerialNumbers

        # Test duplicates
        with self.assertRaises(ValidationError):
            e("1,2,3,3,3", 5)

        # Test invalid length
        with self.assertRaises(ValidationError):
            e("1,2,3", 5)

        # Test empty string
        with self.assertRaises(ValidationError):
            e(", , ,", 0)

        # Test incorrect sign in group
        with self.assertRaises(ValidationError):
            e("10-2", 8)

        # Test invalid group
        with self.assertRaises(ValidationError):
            e("1-5-10", 10)

        with self.assertRaises(ValidationError):
            e("10, a, 7-70j", 4)
