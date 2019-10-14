"""
Django views for interacting with Part app
"""

# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.shortcuts import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.urls import reverse, reverse_lazy
from django.views.generic import DetailView, ListView, FormView
from django.forms.models import model_to_dict
from django.forms import HiddenInput, CheckboxInput

from fuzzywuzzy import fuzz
from decimal import Decimal

from .models import PartCategory, Part, PartAttachment
from .models import PartParameterTemplate, PartParameter
from .models import BomItem
from .models import match_part_names

from common.models import Currency
from company.models import SupplierPart

from . import forms as part_forms
from .bom import MakeBomTemplate, BomUploadManager, ExportBom, IsValidBOMFormat

from .admin import PartResource

from InvenTree.views import AjaxView, AjaxCreateView, AjaxUpdateView, AjaxDeleteView
from InvenTree.views import QRCodeView

from InvenTree.helpers import DownloadFile, str2bool
from InvenTree.status_codes import OrderStatus


class PartIndex(ListView):
    """ View for displaying list of Part objects
    """
    model = Part
    template_name = 'part/category.html'
    context_object_name = 'parts'

    def get_queryset(self):
        return Part.objects.all().select_related('category')

    def get_context_data(self, **kwargs):

        context = super(PartIndex, self).get_context_data(**kwargs).copy()

        # View top-level categories
        children = PartCategory.objects.filter(parent=None)

        context['children'] = children
        context['category_count'] = PartCategory.objects.count()
        context['part_count'] = Part.objects.count()

        return context


class PartAttachmentCreate(AjaxCreateView):
    """ View for creating a new PartAttachment object

    - The view only makes sense if a Part object is passed to it
    """
    model = PartAttachment
    form_class = part_forms.EditPartAttachmentForm
    ajax_form_title = "Add part attachment"
    ajax_template_name = "modal_form.html"

    def get_data(self):
        return {
            'success': 'Added attachment'
        }

    def get_initial(self):
        """ Get initial data for new PartAttachment object.

        - Client should have requested this form with a parent part in mind
        - e.g. ?part=<pk>
        """

        initials = super(AjaxCreateView, self).get_initial()

        # TODO - If the proper part was not sent, return an error message
        try:
            initials['part'] = Part.objects.get(id=self.request.GET.get('part', None))
        except (ValueError, Part.DoesNotExist):
            pass

        return initials

    def get_form(self):
        """ Create a form to upload a new PartAttachment

        - Hide the 'part' field
        """

        form = super(AjaxCreateView, self).get_form()

        form.fields['part'].widget = HiddenInput()

        return form


class PartAttachmentEdit(AjaxUpdateView):
    """ View for editing a PartAttachment object """
    model = PartAttachment
    form_class = part_forms.EditPartAttachmentForm
    ajax_template_name = 'modal_form.html'
    ajax_form_title = 'Edit attachment'
    
    def get_data(self):
        return {
            'success': 'Part attachment updated'
        }

    def get_form(self):
        form = super(AjaxUpdateView, self).get_form()

        form.fields['part'].widget = HiddenInput()

        return form


class PartAttachmentDelete(AjaxDeleteView):
    """ View for deleting a PartAttachment """

    model = PartAttachment
    ajax_form_title = "Delete Part Attachment"
    ajax_template_name = "part/attachment_delete.html"
    context_object_name = "attachment"

    def get_data(self):
        return {
            'danger': 'Deleted part attachment'
        }


class PartSetCategory(AjaxUpdateView):
    """ View for settings the part category for multiple parts at once """

    ajax_template_name = 'part/set_category.html'
    ajax_form_title = 'Set Part Category'
    form_class = part_forms.SetPartCategoryForm

    category = None
    parts = []
    
    def get(self, request, *args, **kwargs):
        """ Respond to a GET request to this view """

        self.request = request

        if 'parts[]' in request.GET:
            self.parts = Part.objects.filter(id__in=request.GET.getlist('parts[]'))
        else:
            self.parts = []

        return self.renderJsonResponse(request, form=self.get_form(), context=self.get_context_data())

    def post(self, request, *args, **kwargs):
        """ Respond to a POST request to this view """

        self.parts = []

        for item in request.POST:
            if item.startswith('part_id_'):
                pk = item.replace('part_id_', '')

                try:
                    part = Part.objects.get(pk=pk)
                except (Part.DoesNotExist, ValueError):
                    continue

                self.parts.append(part)

        self.category = None

        if 'part_category' in request.POST:
            pk = request.POST['part_category']

            try:
                self.category = PartCategory.objects.get(pk=pk)
            except (PartCategory.DoesNotExist, ValueError):
                self.category = None

        valid = self.category is not None

        data = {
            'form_valid': valid,
            'success': _('Set category for {n} parts'.format(n=len(self.parts)))
        }

        if valid:
            self.set_category()

        return self.renderJsonResponse(request, data=data, form=self.get_form(), context=self.get_context_data())

    @transaction.atomic
    def set_category(self):
        for part in self.parts:
            part.set_category(self.category)

    def get_context_data(self):
        """ Return context data for rendering in the form """
        ctx = {}

        ctx['parts'] = self.parts
        ctx['categories'] = PartCategory.objects.all()
        ctx['category'] = self.category

        return ctx
        

class MakePartVariant(AjaxCreateView):
    """ View for creating a new variant based on an existing template Part

    - Part <pk> is provided in the URL '/part/<pk>/make_variant/'
    - Automatically copy relevent data (BOM, etc, etc)

    """

    model = Part
    form_class = part_forms.EditPartForm

    ajax_form_title = 'Create Variant'
    ajax_template_name = 'part/variant_part.html'

    def get_part_template(self):
        return get_object_or_404(Part, id=self.kwargs['pk'])

    def get_context_data(self):
        return {
            'part': self.get_part_template(),
        }

    def get_form(self):
        form = super(AjaxCreateView, self).get_form()

        # Hide some variant-related fields
        form.fields['is_template'].widget = HiddenInput()
        form.fields['variant_of'].widget = HiddenInput()

        return form

    def post(self, request, *args, **kwargs):

        form = self.get_form()
        context = self.get_context_data()
        part_template = self.get_part_template()

        valid = form.is_valid()

        data = {
            'form_valid': valid,
        }

        if valid:
            # Create the new part variant
            part = form.save(commit=False)
            part.variant_of = part_template
            part.is_template = False

            part.save()

            data['pk'] = part.pk
            data['text'] = str(part)
            data['url'] = part.get_absolute_url()

            # Copy relevent information from the template part
            part.deepCopy(part_template, bom=True)

        return self.renderJsonResponse(request, form, data, context=context)

    def get_initial(self):

        part_template = self.get_part_template()

        initials = model_to_dict(part_template)
        initials['is_template'] = False
        initials['variant_of'] = part_template

        return initials


class PartDuplicate(AjaxCreateView):
    """ View for duplicating an existing Part object.

    - Part <pk> is provided in the URL '/part/<pk>/copy/'
    - Option for 'deep-copy' which will duplicate all BOM items (default = True)
    """

    model = Part
    form_class = part_forms.EditPartForm

    ajax_form_title = "Duplicate Part"
    ajax_template_name = "part/copy_part.html"

    def get_data(self):
        return {
            'success': 'Copied part'
        }

    def get_part_to_copy(self):
        try:
            return Part.objects.get(id=self.kwargs['pk'])
        except (Part.DoesNotExist, ValueError):
            return None

    def get_context_data(self):
        return {
            'part': self.get_part_to_copy()
        }

    def get_form(self):
        form = super(AjaxCreateView, self).get_form()

        # Force display of the 'deep_copy' widget
        form.fields['deep_copy'].widget = CheckboxInput()

        return form

    def post(self, request, *args, **kwargs):
        """ Capture the POST request for part duplication

        - If the deep_copy object is set, copy all the BOM items too!
        """

        form = self.get_form()

        context = self.get_context_data()

        valid = form.is_valid()

        name = request.POST.get('name', None)
        
        if name:
            matches = match_part_names(name)

            if len(matches) > 0:
                context['matches'] = matches
            
                # Enforce display of the checkbox
                form.fields['confirm_creation'].widget = CheckboxInput()
                
                # Check if the user has checked the 'confirm_creation' input
                confirmed = str2bool(request.POST.get('confirm_creation', False))

                if not confirmed:
                    form.errors['confirm_creation'] = ['Possible matches exist - confirm creation of new part']
                    
                    form.pre_form_warning = 'Possible matches exist - confirm creation of new part'
                    valid = False

        data = {
            'form_valid': valid
        }

        if valid:
            # Create the new Part
            part = form.save()

            data['pk'] = part.pk
            data['text'] = str(part)

            deep_copy = str2bool(request.POST.get('deep_copy', False))

            original = self.get_part_to_copy()

            if original:
                part.deepCopy(original, bom=deep_copy)

            try:
                data['url'] = part.get_absolute_url()
            except AttributeError:
                pass

        if valid:
            pass

        return self.renderJsonResponse(request, form, data, context=context)

    def get_initial(self):
        """ Get initial data based on the Part to be copied from.
        """

        part = self.get_part_to_copy()

        if part:
            initials = model_to_dict(part)
        else:
            initials = super(AjaxCreateView, self).get_initial()

        return initials


class PartCreate(AjaxCreateView):
    """ View for creating a new Part object.

    Options for providing initial conditions:
    
    - Provide a category object as initial data
    """
    model = Part
    form_class = part_forms.EditPartForm

    ajax_form_title = 'Create new part'
    ajax_template_name = 'part/create_part.html'

    def get_data(self):
        return {
            'success': "Created new part",
        }

    def get_category_id(self):
        return self.request.GET.get('category', None)

    def get_context_data(self, **kwargs):
        """ Provide extra context information for the form to display:

        - Add category information (if provided)
        """
        context = super(PartCreate, self).get_context_data(**kwargs)

        # Add category information to the page
        cat_id = self.get_category_id()

        if cat_id:
            try:
                context['category'] = PartCategory.objects.get(pk=cat_id)
            except (PartCategory.DoesNotExist, ValueError):
                pass

        return context

    def get_form(self):
        """ Create Form for making new Part object.
        Remove the 'default_supplier' field as there are not yet any matching SupplierPart objects
        """
        form = super(AjaxCreateView, self).get_form()

        # Hide the default_supplier field (there are no matching supplier parts yet!)
        form.fields['default_supplier'].widget = HiddenInput()

        return form

    def post(self, request, *args, **kwargs):

        form = self.get_form()

        context = {}

        valid = form.is_valid()
        
        name = request.POST.get('name', None)
        
        if name:
            matches = match_part_names(name)

            if len(matches) > 0:
                context['matches'] = matches
            
                # Enforce display of the checkbox
                form.fields['confirm_creation'].widget = CheckboxInput()
                
                # Check if the user has checked the 'confirm_creation' input
                confirmed = str2bool(request.POST.get('confirm_creation', False))

                if not confirmed:
                    form.errors['confirm_creation'] = ['Possible matches exist - confirm creation of new part']
                    
                    form.pre_form_warning = 'Possible matches exist - confirm creation of new part'
                    valid = False

        data = {
            'form_valid': valid
        }

        if valid:
            # Create the new Part
            part = form.save()

            data['pk'] = part.pk
            data['text'] = str(part)

            try:
                data['url'] = part.get_absolute_url()
            except AttributeError:
                pass

        return self.renderJsonResponse(request, form, data, context=context)

    def get_initial(self):
        """ Get initial data for the new Part object:

        - If a category is provided, pre-fill the Category field
        """

        initials = super(PartCreate, self).get_initial()

        if self.get_category_id():
            try:
                category = PartCategory.objects.get(pk=self.get_category_id())
                initials['category'] = category
                initials['keywords'] = category.default_keywords
            except (PartCategory.DoesNotExist, ValueError):
                pass
        
        # Allow initial data to be passed through as arguments
        for label in ['name', 'IPN', 'description', 'revision', 'keywords']:
            if label in self.request.GET:
                initials[label] = self.request.GET.get(label)

        return initials


class PartDetail(DetailView):
    """ Detail view for Part object
    """

    context_object_name = 'part'
    queryset = Part.objects.all().select_related('category')
    template_name = 'part/detail.html'

    # Add in some extra context information based on query params
    def get_context_data(self, **kwargs):
        """ Provide extra context data to template

        - If '?editing=True', set 'editing_enabled' context variable
        """
        context = super(PartDetail, self).get_context_data(**kwargs)
        
        part = self.get_object()

        if str2bool(self.request.GET.get('edit', '')):
            # Allow BOM editing if the part is active
            context['editing_enabled'] = 1 if part.active else 0
        else:
            context['editing_enabled'] = 0

        context['starred'] = part.isStarredBy(self.request.user)
        context['disabled'] = not part.active

        context['OrderStatus'] = OrderStatus

        return context


class PartQRCode(QRCodeView):
    """ View for displaying a QR code for a Part object """

    ajax_form_title = "Part QR Code"

    def get_qr_data(self):
        """ Generate QR code data for the Part """

        try:
            part = Part.objects.get(id=self.pk)
            return part.format_barcode()
        except Part.DoesNotExist:
            return None


class PartImage(AjaxUpdateView):
    """ View for uploading Part image """

    model = Part
    ajax_template_name = 'modal_form.html'
    ajax_form_title = 'Upload Part Image'
    form_class = part_forms.PartImageForm

    def get_data(self):
        return {
            'success': 'Updated part image',
        }


class PartEdit(AjaxUpdateView):
    """ View for editing Part object """

    model = Part
    form_class = part_forms.EditPartForm
    ajax_template_name = 'modal_form.html'
    ajax_form_title = 'Edit Part Properties'
    context_object_name = 'part'

    def get_form(self):
        """ Create form for Part editing.
        Overrides default get_form() method to limit the choices
        for the 'default_supplier' field to SupplierParts that reference this part
        """

        form = super(AjaxUpdateView, self).get_form()

        part = self.get_object()

        form.fields['default_supplier'].queryset = SupplierPart.objects.filter(part=part)

        return form


class BomValidate(AjaxUpdateView):
    """ Modal form view for validating a part BOM """

    model = Part
    ajax_form_title = "Validate BOM"
    ajax_template_name = 'part/bom_validate.html'
    context_object_name = 'part'
    form_class = part_forms.BomValidateForm

    def get_context(self):
        return {
            'part': self.get_object(),
        }

    def get(self, request, *args, **kwargs):

        form = self.get_form()

        return self.renderJsonResponse(request, form, context=self.get_context())

    def post(self, request, *args, **kwargs):

        form = self.get_form()
        part = self.get_object()

        confirmed = str2bool(request.POST.get('validate', False))

        if confirmed:
            part.validate_bom(request.user)
        else:
            form.errors['validate'] = ['Confirm that the BOM is valid']

        data = {
            'form_valid': confirmed
        }

        return self.renderJsonResponse(request, form, data, context=self.get_context())


class BomUpload(FormView):
    """ View for uploading a BOM file, and handling BOM data importing.

    The BOM upload process is as follows:

    1. (Client) Select and upload BOM file
    2. (Server) Verify that supplied file is a file compatible with tablib library
    3. (Server) Introspect data file, try to find sensible columns / values / etc
    4. (Server) Send suggestions back to the client
    5. (Client) Makes choices based on suggestions:
        - Accept automatic matching to parts found in database
        - Accept suggestions for 'partial' or 'fuzzy' matches
        - Create new parts in case of parts not being available
    6. (Client) Sends updated dataset back to server
    7. (Server) Check POST data for validity, sanity checking, etc.
    8. (Server) Respond to POST request
        - If data are valid, proceed to 9.
        - If data not valid, return to 4.
    9. (Server) Send confirmation form to user
        - Display the actions which will occur
        - Provide final "CONFIRM" button
    10. (Client) Confirm final changes
    11. (Server) Apply changes to database, update BOM items.

    During these steps, data are passed between the server/client as JSON objects.
    """

    template_name = 'part/bom_upload/upload_file.html'

    # Context data passed to the forms (initially empty, extracted from uploaded file)
    bom_headers = []
    bom_columns = []
    bom_rows = []
    missing_columns = []
    allowed_parts = []

    def get_success_url(self):
        part = self.get_object()
        return reverse('upload-bom', kwargs={'pk': part.id})

    def get_form_class(self):

        # Default form is the starting point
        return part_forms.BomUploadSelectFile

    def get_context_data(self, *args, **kwargs):

        ctx = super().get_context_data(*args, **kwargs)

        # Give each row item access to the column it is in
        # This provides for much simpler template rendering

        rows = []
        for row in self.bom_rows:
            row_data = row['data']

            data = []

            for idx, item in enumerate(row_data):

                data.append({
                    'cell': item,
                    'idx': idx,
                    'column': self.bom_columns[idx]
                })

            rows.append({
                'index': row.get('index', -1),
                'data': data,
                'part_options': row.get('part_options', self.allowed_parts),

                # User-input (passed between client and server)
                'quantity': row.get('quantity', None),
                'description': row.get('description', ''),
                'part_name': row.get('part_name', ''),
                'part': row.get('part', None),
                'reference': row.get('reference', ''),
                'notes': row.get('notes', ''),
                'errors': row.get('errors', ''),
            })

        ctx['part'] = self.part
        ctx['bom_headers'] = BomUploadManager.HEADERS
        ctx['bom_columns'] = self.bom_columns
        ctx['bom_rows'] = rows
        ctx['missing_columns'] = self.missing_columns
        ctx['allowed_parts_list'] = self.allowed_parts

        return ctx

    def getAllowedParts(self):
        """ Return a queryset of parts which are allowed to be added to this BOM.
        """

        return self.part.get_allowed_bom_items()

    def get(self, request, *args, **kwargs):
        """ Perform the initial 'GET' request.

        Initially returns a form for file upload """

        self.request = request

        # A valid Part object must be supplied. This is the 'parent' part for the BOM
        self.part = get_object_or_404(Part, pk=self.kwargs['pk'])

        self.form = self.get_form()

        form_class = self.get_form_class()
        form = self.get_form(form_class)
        return self.render_to_response(self.get_context_data(form=form))

    def handleBomFileUpload(self):
        """ Process a BOM file upload form.
        
        This function validates that the uploaded file was valid,
        and contains tabulated data that can be extracted.
        If the file does not satisfy these requirements,
        the "upload file" form is again shown to the user.
         """

        bom_file = self.request.FILES.get('bom_file', None)

        manager = None
        bom_file_valid = False

        if bom_file is None:
            self.form.errors['bom_file'] = [_('No BOM file provided')]
        else:
            # Create a BomUploadManager object - will perform initial data validation
            # (and raise a ValidationError if there is something wrong with the file)
            try:
                manager = BomUploadManager(bom_file)
                bom_file_valid = True
            except ValidationError as e:
                errors = e.error_dict

                for k, v in errors.items():
                    self.form.errors[k] = v

        if bom_file_valid:
            # BOM file is valid? Proceed to the next step!
            form = None
            self.template_name = 'part/bom_upload/select_fields.html'

            self.extractDataFromFile(manager)
        else:
            form = self.form

        return self.render_to_response(self.get_context_data(form=form))

    def getColumnIndex(self, name):
        """ Return the index of the column with the given name.
        It named column is not found, return -1
        """

        try:
            idx = list(self.column_selections.values()).index(name)
        except ValueError:
            idx = -1

        return idx

    def preFillSelections(self):
        """ Once data columns have been selected, attempt to pre-select the proper data from the database.
        This function is called once the field selection has been validated.
        The pre-fill data are then passed through to the part selection form.
        """

        q_idx = self.getColumnIndex('Quantity')
        p_idx = self.getColumnIndex('Part')
        d_idx = self.getColumnIndex('Description')
        r_idx = self.getColumnIndex('Reference')
        n_idx = self.getColumnIndex('Notes')

        for row in self.bom_rows:

            quantity = 0
            part = None

            if q_idx >= 0:
                q_val = row['data'][q_idx]

                try:
                    quantity = int(q_val)
                except ValueError:
                    pass

            if p_idx >= 0:
                part_name = row['data'][p_idx]

                row['part_name'] = part_name

                # Fuzzy match the values and see what happends
                matches = []

                for part in self.allowed_parts:
                    ratio = fuzz.partial_ratio(part.name + part.description, part_name)
                    matches.append({'part': part, 'match': ratio})

                if len(matches) > 0:
                    matches = sorted(matches, key=lambda item: item['match'], reverse=True)

            if d_idx >= 0:
                row['description'] = row['data'][d_idx]

            if r_idx >= 0:
                row['reference'] = row['data'][r_idx]

            if n_idx >= 0:
                row['notes'] = row['data'][n_idx]

            row['quantity'] = quantity
            row['part_options'] = [m['part'] for m in matches]

    def extractDataFromFile(self, bom):
        """ Read data from the BOM file """

        self.bom_columns = bom.columns()
        self.bom_rows = bom.rows()

    def getTableDataFromPost(self):
        """ Extract table cell data from POST request.
        These data are used to maintain state between sessions.

        Table data keys are as follows:

            col_name_<idx> - Column name at idx as provided in the uploaded file
            col_guess_<idx> - Column guess at idx as selected in the BOM
            row_<x>_col<y> - Cell data as provided in the uploaded file

        """

        # Map the columns
        self.column_names = {}
        self.column_selections = {}

        self.row_data = {}

        for item in self.request.POST:
            value = self.request.POST[item]

            # Column names as passed as col_name_<idx> where idx is an integer

            # Extract the column names
            if item.startswith('col_name_'):
                try:
                    col_id = int(item.replace('col_name_', ''))
                except ValueError:
                    continue
                col_name = value

                self.column_names[col_id] = col_name

            # Extract the column selections (in the 'select fields' view)
            if item.startswith('col_guess_'):

                try:
                    col_id = int(item.replace('col_guess_', ''))
                except ValueError:
                    continue

                col_name = value

                self.column_selections[col_id] = value

            # Extract the row data
            if item.startswith('row_'):
                # Item should be of the format row_<r>_col_<c>
                s = item.split('_')

                if len(s) < 4:
                    continue

                # Ignore row/col IDs which are not correct numeric values
                try:
                    row_id = int(s[1])
                    col_id = int(s[3])
                except ValueError:
                    continue
                
                if row_id not in self.row_data:
                    self.row_data[row_id] = {}

                self.row_data[row_id][col_id] = value

        self.col_ids = sorted(self.column_names.keys())

        # Re-construct the data table
        self.bom_rows = []

        for row_idx in sorted(self.row_data.keys()):
            row = self.row_data[row_idx]
            items = []

            for col_idx in sorted(row.keys()):

                value = row[col_idx]
                items.append(value)

            self.bom_rows.append({
                'index': row_idx,
                'data': items,
                'errors': {},
            })

        # Construct the column data
        self.bom_columns = []

        # Track any duplicate column selections
        self.duplicates = False

        for col in self.col_ids:

            if col in self.column_selections:
                guess = self.column_selections[col]
            else:
                guess = None

            header = ({
                'name': self.column_names[col],
                'guess': guess
            })

            if guess:
                n = list(self.column_selections.values()).count(self.column_selections[col])
                if n > 1:
                    header['duplicate'] = True
                    self.duplicates = True

            self.bom_columns.append(header)

        # Are there any missing columns?
        self.missing_columns = []

        for col in BomUploadManager.REQUIRED_HEADERS:
            if col not in self.column_selections.values():
                self.missing_columns.append(col)

    def handleFieldSelection(self):
        """ Handle the output of the field selection form.
        Here the user is presented with the raw data and must select the
        column names and which rows to process.
        """

        # Extract POST data
        self.getTableDataFromPost()

        valid = len(self.missing_columns) == 0 and not self.duplicates
        
        if valid:
            # Try to extract meaningful data
            self.preFillSelections()
            self.template_name = 'part/bom_upload/select_parts.html'
        else:
            self.template_name = 'part/bom_upload/select_fields.html'

        return self.render_to_response(self.get_context_data(form=None))

    def handlePartSelection(self):
        
        # Extract basic table data from POST request
        self.getTableDataFromPost()

        # Keep track of the parts that have been selected
        parts = {}

        # Extract other data (part selections, etc)
        for key in self.request.POST:
            value = self.request.POST[key]

            # Extract quantity from each row
            if key.startswith('quantity_'):
                try:
                    row_id = int(key.replace('quantity_', ''))

                    row = self.getRowByIndex(row_id)

                    if row is None:
                        continue

                    q = 1

                    try:
                        q = int(value)
                        if q <= 0:
                            row['errors']['quantity'] = _('Quantity must be greater than zero')
                    except ValueError:
                        row['errors']['quantity'] = _('Enter a valid quantity')

                    row['quantity'] = q
                     
                except ValueError:
                    continue

            # Extract part from each row
            if key.startswith('part_'):
                try:
                    row_id = int(key.replace('part_', ''))

                    row = self.getRowByIndex(row_id)

                    if row is None:
                        continue
                except ValueError:
                    # Row ID non integer value
                    continue

                try:
                    part_id = int(value)
                    part = Part.objects.get(id=part_id)
                except ValueError:
                    row['errors']['part'] = _('Select valid part')
                    continue
                except Part.DoesNotExist:
                    row['errors']['part'] = _('Select valid part')
                    continue

                # Keep track of how many of each part we have seen
                if part_id in parts:
                    parts[part_id]['quantity'] += 1
                    row['errors']['part'] = _('Duplicate part selected')
                else:
                    parts[part_id] = {
                        'part': part,
                        'quantity': 1,
                    }

                row['part'] = part

            # Extract other fields which do not require further validation
            for field in ['reference', 'notes']:
                if key.startswith(field + '_'):
                    try:
                        row_id = int(key.replace(field + '_', ''))
                        
                        row = self.getRowByIndex(row_id)

                        if row:
                            row[field] = value
                    except:
                        continue

        # Are there any errors after form handling?
        valid = True

        for row in self.bom_rows:
            # Has a part been selected for the given row?
            if row.get('part', None) is None:
                row['errors']['part'] = _('Select a part')

            # Has a quantity been specified?
            if row.get('quantity', None) is None:
                row['errors']['quantity'] = _('Specify quantity')

            errors = row.get('errors', [])

            if len(errors) > 0:
                valid = False

        self.template_name = 'part/bom_upload/select_parts.html'

        ctx = self.get_context_data(form=None)

        if valid:
            self.part.clear_bom()

            # Generate new BOM items
            for row in self.bom_rows:
                part = row.get('part')
                quantity = row.get('quantity')
                reference = row.get('reference', '')
                notes = row.get('notes', '')

                # Create a new BOM item!
                item = BomItem(
                    part=self.part,
                    sub_part=part,
                    quantity=quantity,
                    reference=reference,
                    note=notes
                )

                item.save()

            # Redirect to the BOM view
            return HttpResponseRedirect(reverse('part-bom', kwargs={'pk': self.part.id}))
        else:
            ctx['form_errors'] = True

        return self.render_to_response(ctx)

    def getRowByIndex(self, idx):
        
        for row in self.bom_rows:
            if row['index'] == idx:
                return row

        return None

    def post(self, request, *args, **kwargs):
        """ Perform the various 'POST' requests required.
        """

        self.request = request

        self.part = get_object_or_404(Part, pk=self.kwargs['pk'])
        self.allowed_parts = self.getAllowedParts()
        self.form = self.get_form(self.get_form_class())

        # Did the user POST a file named bom_file?
        
        form_step = request.POST.get('form_step', None)

        if form_step == 'select_file':
            return self.handleBomFileUpload()
        elif form_step == 'select_fields':
            return self.handleFieldSelection()
        elif form_step == 'select_parts':
            return self.handlePartSelection()

        return self.render_to_response(self.get_context_data(form=self.form))


class PartExport(AjaxView):
    """ Export a CSV file containing information on multiple parts """

    def get_parts(self, request):
        """ Extract part list from the POST parameters.
        Parts can be supplied as:
        
        - Part category
        - List of part PK values
        """

        # Filter by part category
        cat_id = request.GET.get('category', None)

        print('cat_id:', cat_id)

        part_list = None

        if cat_id is not None:
            try:
                category = PartCategory.objects.get(pk=cat_id)
                part_list = category.get_parts()
            except (ValueError, PartCategory.DoesNotExist):
                pass

        # Backup - All parts
        if part_list is None:
            part_list = Part.objects.all()

        # Also optionally filter by explicit list of part IDs
        part_ids = request.GET.get('parts', '')
        parts = []

        for pk in part_ids.split(','):
            try:
                parts.append(int(pk))
            except ValueError:
                pass

        if len(parts) > 0:
            part_list = part_list.filter(pk__in=parts)

        # Prefetch related fields to reduce DB hits
        part_list = part_list.prefetch_related(
            'category',
            'used_in',
            'builds',
            'supplier_parts__purchase_order_line_items',
            'stock_items__allocations',
        )

        return part_list

    def get(self, request, *args, **kwargs):

        parts = self.get_parts(request)

        dataset = PartResource().export(queryset=parts)

        csv = dataset.export('csv')
        return DownloadFile(csv, 'InvenTree_Parts.csv')


class BomUploadTemplate(AjaxView):
    """
    Provide a BOM upload template file for download.
    - Generates a template file in the provided format e.g. ?format=csv
    """

    def get(self, request, *args, **kwargs):

        export_format = request.GET.get('format', 'csv')

        return MakeBomTemplate(export_format)


class BomDownload(AjaxView):
    """
    Provide raw download of a BOM file.
    - File format should be passed as a query param e.g. ?format=csv
    """

    model = Part

    def get(self, request, *args, **kwargs):

        part = get_object_or_404(Part, pk=self.kwargs['pk'])

        export_format = request.GET.get('format', 'csv')

        if not IsValidBOMFormat(export_format):
            export_format = 'csv'

        return ExportBom(part, fmt=export_format)

    def get_data(self):
        return {
            'info': 'Exported BOM'
        }


class PartDelete(AjaxDeleteView):
    """ View to delete a Part object """

    model = Part
    ajax_template_name = 'part/partial_delete.html'
    ajax_form_title = 'Confirm Part Deletion'
    context_object_name = 'part'

    success_url = '/part/'

    def get_data(self):
        return {
            'danger': 'Part was deleted',
        }


class PartPricing(AjaxView):
    """ View for inspecting part pricing information """

    model = Part
    ajax_template_name = "part/part_pricing.html"
    ajax_form_title = "Part Pricing"
    form_class = part_forms.PartPriceForm

    def get_part(self):
        try:
            return Part.objects.get(id=self.kwargs['pk'])
        except Part.DoesNotExist:
            return None

    def get_pricing(self, quantity=1, currency=None):

        try:
            quantity = int(quantity)
        except ValueError:
            quantity = 1

        if quantity < 1:
            quantity = 1

        if currency is None:
            # No currency selected? Try to select a default one
            try:
                currency = Currency.objects.get(base=1)
            except Currency.DoesNotExist:
                currency = None

        # Currency scaler
        scaler = Decimal(1.0)

        if currency is not None:
            scaler = Decimal(currency.value)

        part = self.get_part()
        
        ctx = {
            'part': part,
            'quantity': quantity,
            'currency': currency,
        }

        if part is None:
            return ctx

        # Supplier pricing information
        if part.supplier_count > 0:
            buy_price = part.get_supplier_price_range(quantity)

            if buy_price is not None:
                min_buy_price, max_buy_price = buy_price

                min_buy_price /= scaler
                max_buy_price /= scaler

                min_buy_price = round(min_buy_price, 3)
                max_buy_price = round(max_buy_price, 3)

                if min_buy_price:
                    ctx['min_total_buy_price'] = min_buy_price
                    ctx['min_unit_buy_price'] = min_buy_price / quantity

                if max_buy_price:
                    ctx['max_total_buy_price'] = max_buy_price
                    ctx['max_unit_buy_price'] = max_buy_price / quantity

        # BOM pricing information
        if part.bom_count > 0:

            bom_price = part.get_bom_price_range(quantity)

            if bom_price is not None:
                min_bom_price, max_bom_price = bom_price

                min_bom_price /= scaler
                max_bom_price /= scaler

                min_bom_price = round(min_bom_price, 3)
                max_bom_price = round(max_bom_price, 3)

                if min_bom_price:
                    ctx['min_total_bom_price'] = min_bom_price
                    ctx['min_unit_bom_price'] = min_bom_price / quantity
                
                if max_bom_price:
                    ctx['max_total_bom_price'] = max_bom_price
                    ctx['max_unit_bom_price'] = max_bom_price / quantity

        return ctx

    def get(self, request, *args, **kwargs):

        return self.renderJsonResponse(request, self.form_class(), context=self.get_pricing())

    def post(self, request, *args, **kwargs):

        currency = None

        try:
            quantity = int(self.request.POST.get('quantity', 1))
        except ValueError:
            quantity = 1

        try:
            currency_id = int(self.request.POST.get('currency', None))

            if currency_id:
                currency = Currency.objects.get(pk=currency_id)
        except (ValueError, Currency.DoesNotExist):
            currency = None

        # Always mark the form as 'invalid' (the user may wish to keep getting pricing data)
        data = {
            'form_valid': False,
        }

        return self.renderJsonResponse(request, self.form_class(), data=data, context=self.get_pricing(quantity, currency))


class PartParameterTemplateCreate(AjaxCreateView):
    """ View for creating a new PartParameterTemplate """

    model = PartParameterTemplate
    form_class = part_forms.EditPartParameterTemplateForm
    ajax_form_title = 'Create Part Parameter Template'


class PartParameterTemplateEdit(AjaxUpdateView):
    """ View for editing a PartParameterTemplate """

    model = PartParameterTemplate
    form_class = part_forms.EditPartParameterTemplateForm
    ajax_form_title = 'Edit Part Parameter Template'


class PartParameterTemplateDelete(AjaxDeleteView):
    """ View for deleting an existing PartParameterTemplate """

    model = PartParameterTemplate
    ajax_form_title = "Delete Part Parameter Template"


class PartParameterCreate(AjaxCreateView):
    """ View for creating a new PartParameter """

    model = PartParameter
    form_class = part_forms.EditPartParameterForm
    ajax_form_title = 'Create Part Parameter'

    def get_initial(self):

        initials = {}

        part_id = self.request.GET.get('part', None)

        if part_id:
            try:
                initials['part'] = Part.objects.get(pk=part_id)
            except (Part.DoesNotExist, ValueError):
                pass

        return initials

    def get_form(self):
        """ Return the form object.

        - Hide the 'Part' field (specified in URL)
        - Limit the 'Template' options (to avoid duplicates)
        """

        form = super().get_form()

        part_id = self.request.GET.get('part', None)

        if part_id:
            try:
                part = Part.objects.get(pk=part_id)

                form.fields['part'].widget = HiddenInput()

                query = form.fields['template'].queryset

                query = query.exclude(id__in=[param.template.id for param in part.parameters.all()])

                form.fields['template'].queryset = query

            except (Part.DoesNotExist, ValueError):
                pass

        return form


class PartParameterEdit(AjaxUpdateView):
    """ View for editing a PartParameter """

    model = PartParameter
    form_class = part_forms.EditPartParameterForm
    ajax_form_title = 'Edit Part Parameter'

    def get_form(self):

        form = super().get_form()

        return form


class PartParameterDelete(AjaxDeleteView):
    """ View for deleting a PartParameter """

    model = PartParameter
    ajax_template_name = 'part/param_delete.html'
    ajax_form_title = 'Delete Part Parameter'
    

class CategoryDetail(DetailView):
    """ Detail view for PartCategory """
    model = PartCategory
    context_object_name = 'category'
    queryset = PartCategory.objects.all().prefetch_related('children')
    template_name = 'part/category.html'


class CategoryEdit(AjaxUpdateView):
    """ Update view to edit a PartCategory """
    model = PartCategory
    form_class = part_forms.EditCategoryForm
    ajax_template_name = 'modal_form.html'
    ajax_form_title = 'Edit Part Category'

    def get_context_data(self, **kwargs):
        context = super(CategoryEdit, self).get_context_data(**kwargs).copy()

        try:
            context['category'] = self.get_object()
        except:
            pass

        return context

    def get_form(self):
        """ Customize form data for PartCategory editing.

        Limit the choices for 'parent' field to those which make sense
        """
        
        form = super(AjaxUpdateView, self).get_form()
        
        category = self.get_object()

        # Remove any invalid choices for the parent category part
        parent_choices = PartCategory.objects.all()
        parent_choices = parent_choices.exclude(id__in=category.getUniqueChildren())

        form.fields['parent'].queryset = parent_choices

        return form


class CategoryDelete(AjaxDeleteView):
    """ Delete view to delete a PartCategory """
    model = PartCategory
    ajax_template_name = 'part/category_delete.html'
    ajax_form_title = 'Delete Part Category'
    context_object_name = 'category'
    success_url = '/part/'

    def get_data(self):
        return {
            'danger': 'Part category was deleted',
        }


class CategoryCreate(AjaxCreateView):
    """ Create view to make a new PartCategory """
    model = PartCategory
    ajax_form_action = reverse_lazy('category-create')
    ajax_form_title = 'Create new part category'
    ajax_template_name = 'modal_form.html'
    form_class = part_forms.EditCategoryForm

    def get_context_data(self, **kwargs):
        """ Add extra context data to template.

        - If parent category provided, pass the category details to the template
        """
        context = super(CategoryCreate, self).get_context_data(**kwargs).copy()

        parent_id = self.request.GET.get('category', None)

        if parent_id:
            try:
                context['category'] = PartCategory.objects.get(pk=parent_id)
            except PartCategory.DoesNotExist:
                pass

        return context

    def get_initial(self):
        """ Get initial data for new PartCategory

        - If parent provided, pre-fill the parent category
        """
        initials = super(CategoryCreate, self).get_initial().copy()

        parent_id = self.request.GET.get('category', None)

        if parent_id:
            try:
                initials['parent'] = PartCategory.objects.get(pk=parent_id)
            except PartCategory.DoesNotExist:
                pass

        return initials


class BomItemDetail(DetailView):
    """ Detail view for BomItem """
    context_object_name = 'item'
    queryset = BomItem.objects.all()
    template_name = 'part/bom-detail.html'


class BomItemCreate(AjaxCreateView):
    """ Create view for making a new BomItem object """
    model = BomItem
    form_class = part_forms.EditBomItemForm
    ajax_template_name = 'modal_form.html'
    ajax_form_title = 'Create BOM item'

    def get_form(self):
        """ Override get_form() method to reduce Part selection options.

        - Do not allow part to be added to its own BOM
        - Remove any Part items that are already in the BOM
        """

        form = super(AjaxCreateView, self).get_form()

        part_id = form['part'].value()

        try:
            part = Part.objects.get(id=part_id)

            # Only allow active parts to be selected
            query = form.fields['part'].queryset.filter(active=True)
            form.fields['part'].queryset = query

            # Don't allow selection of sub_part objects which are already added to the Bom!
            query = form.fields['sub_part'].queryset
            
            # Don't allow a part to be added to its own BOM
            query = query.exclude(id=part.id)
            query = query.filter(active=True)
            
            # Eliminate any options that are already in the BOM!
            query = query.exclude(id__in=[item.id for item in part.required_parts()])
            
            form.fields['sub_part'].queryset = query

            form.fields['part'].widget = HiddenInput()

        except (ValueError, Part.DoesNotExist):
            pass

        return form

    def get_initial(self):
        """ Provide initial data for the BomItem:

        - If 'parent' provided, set the parent part field
        """

        # Look for initial values
        initials = super(BomItemCreate, self).get_initial().copy()

        # Parent part for this item?
        parent_id = self.request.GET.get('parent', None)

        if parent_id:
            try:
                initials['part'] = Part.objects.get(pk=parent_id)
            except Part.DoesNotExist:
                pass

        return initials


class BomItemEdit(AjaxUpdateView):
    """ Update view for editing BomItem """

    model = BomItem
    form_class = part_forms.EditBomItemForm
    ajax_template_name = 'modal_form.html'
    ajax_form_title = 'Edit BOM item'

    def get_form(self):
        """ Override get_form() method to filter part selection options

        - Do not allow part to be added to its own BOM
        - Remove any part items that are already in the BOM
        """

        form = super().get_form()

        part_id = form['part'].value()

        try:
            part = Part.objects.get(pk=part_id)

            query = form.fields['sub_part'].queryset

            # Reduce the available selection options
            query = query.exclude(pk=part_id)

            # Eliminate any options that are already in the BOM,
            # *except* for the item which is already selected
            try:
                sub_part_id = int(form['sub_part'].value())
            except ValueError:
                sub_part_id = -1

            existing = [item.pk for item in part.required_parts()]

            if sub_part_id in existing:
                existing.remove(sub_part_id)

            query = query.exclude(id__in=existing)

            form.fields['sub_part'].queryset = query

        except (ValueError, Part.DoesNotExist):
            pass

        return form


class BomItemDelete(AjaxDeleteView):
    """ Delete view for removing BomItem """
    model = BomItem
    ajax_template_name = 'part/bom-delete.html'
    context_object_name = 'item'
    ajax_form_title = 'Confim BOM item deletion'
