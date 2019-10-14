"""
Various Views which provide extra functionality over base Django Views.

In particular these views provide base functionality for rendering Django forms
as JSON objects and passing them to modal forms (using jQuery / bootstrap).
"""

# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.template.loader import render_to_string
from django.http import JsonResponse, HttpResponseRedirect

from django.views import View
from django.views.generic import UpdateView, CreateView
from django.views.generic.base import TemplateView

from part.models import Part
from common.models import InvenTreeSetting

from .forms import DeleteForm, EditUserForm, SetPasswordForm
from .helpers import str2bool
from .version import inventreeVersion

from rest_framework import views


class TreeSerializer(views.APIView):
    """ JSON View for serializing a Tree object.

    Turns a 'tree' model into a JSON object compatible with bootstrap-treview.

    Ref: https://github.com/jonmiles/bootstrap-treeview
    """

    @property
    def root_url(self):
        """ Return the root URL for the tree. Implementation is class dependent.

        Default implementation returns #
        """

        return '#'

    def itemToJson(self, item):

        data = {
            'pk': item.id,
            'text': item.name,
            'href': item.get_absolute_url(),
            'tags': [item.item_count],
        }

        if item.has_children:
            nodes = []

            for child in item.children.all().order_by('name'):
                nodes.append(self.itemToJson(child))

            data['nodes'] = nodes

        return data

    def get_items(self):

        return self.model.objects.all()

    def generate_tree(self):

        nodes = []

        items = self.get_items()

        # Construct the top-level items
        top_items = [i for i in items if i.parent is None]

        top_count = 0

        # Construct the top-level items
        top_items = [i for i in items if i.parent is None]

        for item in top_items:
            nodes.append(self.itemToJson(item))
            top_count += item.item_count

        self.tree = {
            'pk': None,
            'text': self.title,
            'href': self.root_url,
            'nodes': nodes,
            'tags': [top_count],
        }

    def get(self, request, *args, **kwargs):
        """ Respond to a GET request for the Tree """

        self.generate_tree()

        response = {
            'tree': [self.tree]
        }

        return JsonResponse(response, safe=False)


class AjaxMixin(object):
    """ AjaxMixin provides basic functionality for rendering a Django form to JSON.
    Handles jsonResponse rendering, and adds extra data for the modal forms to process
    on the client side.
    """

    # By default, point to the modal_form template
    # (this can be overridden by a child class)
    ajax_template_name = 'modal_form.html'

    ajax_form_action = ''
    ajax_form_title = ''

    def get_param(self, name, method='GET'):
        """ Get a request query parameter value from URL e.g. ?part=3

        Args:
            name: Variable name e.g. 'part'
            method: Request type ('GET' or 'POST')

        Returns:
            Value of the supplier parameter or None if parameter is not available
        """

        if method == 'POST':
            return self.request.POST.get(name, None)
        else:
            return self.request.GET.get(name, None)

    def get_data(self):
        """ Get extra context data (default implementation is empty dict)

        Returns:
            dict object (empty)
        """
        return {}

    def renderJsonResponse(self, request, form=None, data={}, context=None):
        """ Render a JSON response based on specific class context.

        Args:
            request: HTTP request object (e.g. GET / POST)
            form: Django form object (may be None)
            data: Extra JSON data to pass to client
            context: Extra context data to pass to template rendering

        Returns:
            JSON response object
        """

        if not request.is_ajax():
            return HttpResponseRedirect('/')

        if context is None:
            try:
                context = self.get_context_data()
            except AttributeError:
                context = {}

        if form:
            context['form'] = form
        else:
            context['form'] = None

        data['title'] = self.ajax_form_title

        data['html_form'] = render_to_string(
            self.ajax_template_name,
            context,
            request=request
        )

        # Custom feedback`data
        fb = self.get_data()

        for key in fb.keys():
            data[key] = fb[key]

        return JsonResponse(data, safe=False)


class AjaxView(AjaxMixin, View):
    """ An 'AJAXified' View for displaying an object
    """

    def post(self, request, *args, **kwargs):
        return self.renderJsonResponse(request)

    def get(self, request, *args, **kwargs):

        return self.renderJsonResponse(request)


class QRCodeView(AjaxView):
    """ An 'AJAXified' view for displaying a QR code.

    Subclasses should implement the get_qr_data(self) function.
    """

    ajax_template_name = "qr_code.html"
    
    def get(self, request, *args, **kwargs):
        self.request = request
        self.pk = self.kwargs['pk']
        return self.renderJsonResponse(request, None, context=self.get_context_data())

    def get_qr_data(self):
        """ Returns the text object to render to a QR code.
        The actual rendering will be handled by the template """
        
        return None

    def get_context_data(self):
        """ Get context data for passing to the rendering template.

        Explicity passes the parameter 'qr_data'
        """
        
        context = {}

        qr = self.get_qr_data()

        if qr:
            context['qr_data'] = qr
        else:
            context['error_msg'] = 'Error generating QR code'
        
        return context


class AjaxCreateView(AjaxMixin, CreateView):

    """ An 'AJAXified' CreateView for creating a new object in the db
    - Returns a form in JSON format (for delivery to a modal window)
    - Handles form validation via AJAX POST requests
    """

    def pre_save(self, **kwargs):
        """
        Hook for doing something before the form is validated
        """
        pass

    def post_save(self, **kwargs):
        """
        Hook for doing something with the created object after it is saved
        """
        pass

    def get(self, request, *args, **kwargs):
        """ Creates form with initial data, and renders JSON response """

        super(CreateView, self).get(request, *args, **kwargs)

        self.request = request
        form = self.get_form()
        return self.renderJsonResponse(request, form)

    def post(self, request, *args, **kwargs):
        """ Responds to form POST. Validates POST data and returns status info.

        - Validate POST form data
        - If valid, save form
        - Return status info (success / failure)
        """
        self.request = request
        self.form = self.get_form()

        # Extra JSON data sent alongside form
        data = {
            'form_valid': self.form.is_valid(),
        }

        if self.form.is_valid():

            self.pre_save()
            self.object = self.form.save()
            self.post_save()

            # Return the PK of the newly-created object
            data['pk'] = self.object.pk
            data['text'] = str(self.object)

            try:
                data['url'] = self.object.get_absolute_url()
            except AttributeError:
                pass

        return self.renderJsonResponse(request, self.form, data)


class AjaxUpdateView(AjaxMixin, UpdateView):
    """ An 'AJAXified' UpdateView for updating an object in the db
    - Returns form in JSON format (for delivery to a modal window)
    - Handles repeated form validation (via AJAX) until the form is valid
    """

    def get(self, request, *args, **kwargs):
        """ Respond to GET request.

        - Populates form with object data
        - Renders form to JSON and returns to client
        """

        super(UpdateView, self).get(request, *args, **kwargs)
        
        return self.renderJsonResponse(request, self.get_form(), context=self.get_context_data())

    def post(self, request, *args, **kwargs):
        """ Respond to POST request.

        - Updates model with POST field data
        - Performs form and object validation
        - If errors exist, re-render the form
        - Otherwise, return sucess status
        """

        # Make sure we have an object to point to
        self.object = self.get_object()

        form = self.get_form()

        data = {
            'form_valid': form.is_valid()
        }

        if form.is_valid():
            obj = form.save()

            # Include context data about the updated object
            data['pk'] = obj.id

            try:
                data['url'] = obj.get_absolute_url()
            except AttributeError:
                pass

        return self.renderJsonResponse(request, form, data)


class AjaxDeleteView(AjaxMixin, UpdateView):

    """ An 'AJAXified DeleteView for removing an object from the DB
    - Returns a HTML object (not a form!) in JSON format (for delivery to a modal window)
    - Handles deletion
    """

    form_class = DeleteForm
    ajax_form_title = "Delete Item"
    ajax_template_name = "modal_delete_form.html"
    context_object_name = 'item'

    def get_object(self):
        try:
            self.object = self.model.objects.get(pk=self.kwargs['pk'])
        except:
            return None
        return self.object

    def get_form(self):
        return self.form_class(self.get_form_kwargs())

    def get(self, request, *args, **kwargs):
        """ Respond to GET request

        - Render a DELETE confirmation form to JSON
        - Return rendered form to client
        """

        super(UpdateView, self).get(request, *args, **kwargs)

        form = self.get_form()

        context = self.get_context_data()

        context[self.context_object_name] = self.get_object()

        return self.renderJsonResponse(request, form, context=context)

    def post(self, request, *args, **kwargs):
        """ Respond to POST request

        - DELETE the object
        - Render success message to JSON and return to client
        """

        obj = self.get_object()
        pk = obj.id

        form = self.get_form()

        confirmed = str2bool(request.POST.get('confirm_delete', False))
        context = self.get_context_data()

        if confirmed:
            obj.delete()
        else:
            form.errors['confirm_delete'] = ['Check box to confirm item deletion']
            context[self.context_object_name] = self.get_object()

        data = {
            'id': pk,
            'form_valid': confirmed
        }

        return self.renderJsonResponse(request, form, data=data, context=context)


class InfoView(AjaxView):
    """ Simple JSON endpoint for InvenTree information.
    Use to confirm that the server is running, etc.
    """

    def get(self, request, *args, **kwargs):

        data = {
            'server': 'InvenTree',
            'version': inventreeVersion()
        }

        return JsonResponse(data)


class EditUserView(AjaxUpdateView):
    """ View for editing user information """

    ajax_template_name = "modal_form.html"
    ajax_form_title = "Edit User Information"
    form_class = EditUserForm

    def get_object(self):
        return self.request.user


class SetPasswordView(AjaxUpdateView):
    """ View for setting user password """

    ajax_template_name = "InvenTree/password.html"
    ajax_form_title = "Set Password"
    form_class = SetPasswordForm

    def get_object(self):
        return self.request.user

    def post(self, request, *args, **kwargs):

        form = self.get_form()

        valid = form.is_valid()

        p1 = request.POST.get('enter_password', '')
        p2 = request.POST.get('confirm_password', '')
        
        if valid:
            # Passwords must match

            if not p1 == p2:
                error = 'Password fields must match'
                form.errors['enter_password'] = [error]
                form.errors['confirm_password'] = [error]

                valid = False

        data = {
            'form_valid': valid
        }

        if valid:
            user = self.request.user

            user.set_password(p1)
            user.save()

        return self.renderJsonResponse(request, form, data=data)


class IndexView(TemplateView):
    """ View for InvenTree index page """

    template_name = 'InvenTree/index.html'

    def get_context_data(self, **kwargs):

        context = super(TemplateView, self).get_context_data(**kwargs)

        context['starred'] = [star.part for star in self.request.user.starred_parts.all()]

        # Generate a list of orderable parts which have stock below their minimum values
        # TODO - Is there a less expensive way to get these from the database
        context['to_order'] = [part for part in Part.objects.filter(purchaseable=True) if part.need_to_restock()]
    
        # Generate a list of assembly parts which have stock below their minimum values
        # TODO - Is there a less expensive way to get these from the database
        context['to_build'] = [part for part in Part.objects.filter(assembly=True) if part.need_to_restock()]

        return context


class SearchView(TemplateView):
    """ View for InvenTree search page.

    Displays results of search query
    """

    template_name = 'InvenTree/search.html'

    def post(self, request, *args, **kwargs):
        """ Handle POST request (which contains search query).

        Pass the search query to the page template
        """

        context = self.get_context_data()

        query = request.POST.get('search', '')

        context['query'] = query

        return super(TemplateView, self).render_to_response(context)


class SettingsView(TemplateView):
    """ View for configuring User settings
    """

    template_name = "InvenTree/settings.html"

    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs).copy()

        ctx['settings'] = InvenTreeSetting.objects.all().order_by('key')

        return ctx
