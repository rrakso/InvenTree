from django.test import TestCase
from django.db.models import Sum
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import StockLocation, StockItem, StockItemTracking
from part.models import Part


class StockTest(TestCase):
    """
    Tests to ensure that the stock location tree functions correcly
    """

    fixtures = [
        'category',
        'part',
        'location',
        'stock',
    ]

    def setUp(self):
        # Extract some shortcuts from the fixtures
        self.home = StockLocation.objects.get(name='Home')
        self.bathroom = StockLocation.objects.get(name='Bathroom')
        self.diningroom = StockLocation.objects.get(name='Dining Room')

        self.office = StockLocation.objects.get(name='Office')
        self.drawer1 = StockLocation.objects.get(name='Drawer_1')
        self.drawer2 = StockLocation.objects.get(name='Drawer_2')
        self.drawer3 = StockLocation.objects.get(name='Drawer_3')

        # Create a user
        User = get_user_model()
        User.objects.create_user('username', 'user@email.com', 'password')

        self.client.login(username='username', password='password')

        self.user = User.objects.get(username='username')

    def test_loc_count(self):
        self.assertEqual(StockLocation.objects.count(), 7)

    def test_url(self):
        it = StockItem.objects.get(pk=2)
        self.assertEqual(it.get_absolute_url(), '/stock/item/2/')

        self.assertEqual(self.home.get_absolute_url(), '/stock/location/1/')

    def test_barcode(self):
        barcode = self.office.format_barcode()

        self.assertIn('"name": "Office"', barcode)

    def test_strings(self):
        it = StockItem.objects.get(pk=1)
        self.assertEqual(str(it), '4000 x M2x4 LPHS @ Dining Room')

    def test_parent_locations(self):

        self.assertEqual(self.office.parent, None)
        self.assertEqual(self.drawer1.parent, self.office)
        self.assertEqual(self.drawer2.parent, self.office)
        self.assertEqual(self.drawer3.parent, self.office)

        self.assertEqual(self.drawer3.pathstring, 'Office/Drawer_3')

        # Move one of the drawers
        self.drawer3.parent = self.home
        self.drawer3.save()

        self.assertNotEqual(self.drawer3.parent, self.office)
        
        self.assertEqual(self.drawer3.pathstring, 'Home/Drawer_3')

    def test_children(self):
        self.assertTrue(self.office.has_children)

        self.assertFalse(self.drawer2.has_children)

        childs = [item.pk for item in self.office.getUniqueChildren()]

        self.assertIn(self.drawer1.id, childs)
        self.assertIn(self.drawer2.id, childs)

        self.assertNotIn(self.bathroom.id, childs)

    def test_items(self):
        self.assertTrue(self.drawer1.has_items())
        self.assertTrue(self.drawer3.has_items())
        self.assertFalse(self.drawer2.has_items())

        # Drawer 3 should have three stock items
        self.assertEqual(self.drawer3.stock_items.count(), 3)
        self.assertEqual(self.drawer3.item_count, 3)

    def test_stock_count(self):
        part = Part.objects.get(pk=1)

        # There should be 5000 screws in stock
        self.assertEqual(part.total_stock, 9000)

        # There should be 18 widgets in stock
        self.assertEqual(StockItem.objects.filter(part=25).aggregate(Sum('quantity'))['quantity__sum'], 18)

    def test_delete_location(self):

        # How many stock items are there?
        n_stock = StockItem.objects.count()

        # What parts are in drawer 3?
        stock_ids = [part.id for part in StockItem.objects.filter(location=self.drawer3.id)]

        # Delete location - parts should move to parent location
        self.drawer3.delete()

        # There should still be the same number of parts
        self.assertEqual(StockItem.objects.count(), n_stock)

        # stock should have moved
        for s_id in stock_ids:
            s_item = StockItem.objects.get(id=s_id)
            self.assertEqual(s_item.location, self.office)

    def test_move(self):
        """ Test stock movement functions """

        # Move 4,000 screws to the bathroom
        it = StockItem.objects.get(pk=1)
        self.assertNotEqual(it.location, self.bathroom)
        self.assertTrue(it.move(self.bathroom, 'Moved to the bathroom', None))
        self.assertEqual(it.location, self.bathroom)

        # There now should be 2 lots of screws in the bathroom
        self.assertEqual(StockItem.objects.filter(part=1, location=self.bathroom).count(), 2)

        # Check that a tracking item was added
        track = StockItemTracking.objects.filter(item=it).latest('id')

        self.assertEqual(track.item, it)
        self.assertIn('Moved to', track.title)
        self.assertEqual(track.notes, 'Moved to the bathroom')

    def test_self_move(self):
        # Try to move an item to its current location (should fail)
        it = StockItem.objects.get(pk=1)

        n = it.tracking_info.count()
        self.assertFalse(it.move(it.location, 'Moved to same place', None))

        # Ensure tracking info was not added
        self.assertEqual(it.tracking_info.count(), n)

    def test_partial_move(self):
        w1 = StockItem.objects.get(pk=100)

        # Move 6 of the units
        self.assertTrue(w1.move(self.diningroom, 'Moved', None, quantity=6))
        self.assertEqual(w1.quantity, 6)

        # There should also be a new object still in drawer3
        self.assertEqual(StockItem.objects.filter(part=25).count(), 4)
        widget = StockItem.objects.get(location=self.drawer3.id, part=25, quantity=4)

        # Try to move negative units
        self.assertFalse(widget.move(self.bathroom, 'Test', None, quantity=-100))
        self.assertEqual(StockItem.objects.filter(part=25).count(), 4)

        # Try to move to a blank location
        self.assertFalse(widget.move(None, 'null', None))

    def test_split_stock(self):
        # Split the 1234 x 2K2 resistors in Drawer_1

        N = StockItem.objects.filter(part=3).count()

        stock = StockItem.objects.get(id=1234)
        stock.splitStock(1000, None)
        self.assertEqual(stock.quantity, 234)

        # There should be a new stock item too!
        self.assertEqual(StockItem.objects.filter(part=3).count(), N + 1)

        # Try to split a negative quantity
        stock.splitStock(-10, None)
        self.assertEqual(StockItem.objects.filter(part=3).count(), N + 1)

        stock.splitStock(stock.quantity, None)
        self.assertEqual(StockItem.objects.filter(part=3).count(), N + 1)

    def test_stocktake(self):
        # Perform stocktake
        it = StockItem.objects.get(pk=2)
        self.assertEqual(it.quantity, 5000)
        it.stocktake(255, None, notes='Counted items!')

        self.assertEqual(it.quantity, 255)

        # Check that a tracking item was added
        track = StockItemTracking.objects.filter(item=it).latest('id')

        self.assertIn('Stocktake', track.title)
        self.assertIn('Counted items', track.notes)

        n = it.tracking_info.count()
        self.assertFalse(it.stocktake(-1, None, 'test negative stocktake'))

        # Ensure tracking info was not added
        self.assertEqual(it.tracking_info.count(), n)

    def test_add_stock(self):
        it = StockItem.objects.get(pk=2)
        n = it.quantity
        it.add_stock(45, None, notes='Added some items')

        self.assertEqual(it.quantity, n + 45)

        # Check that a tracking item was added
        track = StockItemTracking.objects.filter(item=it).latest('id')

        self.assertIn('Added', track.title)
        self.assertIn('Added some items', track.notes)

        self.assertFalse(it.add_stock(-10, None))

    def test_take_stock(self):
        it = StockItem.objects.get(pk=2)
        n = it.quantity
        it.take_stock(15, None, notes='Removed some items')

        self.assertEqual(it.quantity, n - 15)

        # Check that a tracking item was added
        track = StockItemTracking.objects.filter(item=it).latest('id')

        self.assertIn('Removed', track.title)
        self.assertIn('Removed some items', track.notes)
        self.assertTrue(it.has_tracking_info)

        # Test that negative quantity does nothing
        self.assertFalse(it.take_stock(-10, None))

    def test_deplete_stock(self):

        w1 = StockItem.objects.get(pk=100)
        w2 = StockItem.objects.get(pk=101)

        # Take 25 units from w1
        w1.take_stock(30, None, notes='Took 30')

        # Get from database again
        w1 = StockItem.objects.get(pk=100)
        self.assertEqual(w1.quantity, 0)

        # Take 25 units from w2 (will be deleted)
        w2.take_stock(30, None, notes='Took 30')

        with self.assertRaises(StockItem.DoesNotExist):
            w2 = StockItem.objects.get(pk=101)

    def test_serialize_stock_invalid(self):
        """
        Test manual serialization of parts.
        Each of these tests should fail
        """

        # Test serialization of non-serializable part
        item = StockItem.objects.get(pk=1234)

        with self.assertRaises(ValidationError):
            item.serializeStock(5, [1, 2, 3, 4, 5], self.user)

        with self.assertRaises(ValidationError):
            item.serializeStock(5, [1, 2, 3], self.user)

        # Pick a StockItem which can actually be serialized
        item = StockItem.objects.get(pk=100)

        # Try an invalid quantity
        with self.assertRaises(ValidationError):
            item.serializeStock("k", [], self.user)

        with self.assertRaises(ValidationError):
            item.serializeStock(-1, [], self.user)

        # Try invalid serial numbers
        with self.assertRaises(ValidationError):
            item.serializeStock(3, [1, 2, 'k'], self.user)

        with self.assertRaises(ValidationError):
            item.serializeStock(3, "hello", self.user)

    def test_serialize_stock_valid(self):
        """ Perform valid stock serializations """

        # There are 10 of these in stock
        # Item will deplete when deleted
        item = StockItem.objects.get(pk=100)
        item.delete_on_deplete = True
        item.save()

        n = StockItem.objects.filter(part=25).count()

        self.assertEqual(item.quantity, 10)

        item.serializeStock(3, [1, 2, 3], self.user)

        self.assertEqual(item.quantity, 7)

        # Try to serialize again (with same serial numbers)
        with self.assertRaises(ValidationError):
            item.serializeStock(3, [1, 2, 3], self.user)

        # Try to serialize too many items
        with self.assertRaises(ValidationError):
            item.serializeStock(13, [1, 2, 3], self.user)

        # Serialize some more stock
        item.serializeStock(5, [6, 7, 8, 9, 10], self.user)

        self.assertEqual(item.quantity, 2)

        # There should be 8 more items now
        self.assertEqual(StockItem.objects.filter(part=25).count(), n + 8)

        # Serialize the remainder of the stock
        item.serializeStock(2, [99, 100], self.user)

        # Two more items but the original has been deleted
        self.assertEqual(StockItem.objects.filter(part=25).count(), n + 9)
