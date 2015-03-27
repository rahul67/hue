#!/usr/bin/env python
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging

from django.http import HttpResponse
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext as _

from desktop.lib.django_util import JsonResponse
from desktop.models import Document2, Document

from spark.models import get_api, Notebook, QueryExpired
from spark.decorators import api_error_handler, check_document_modify_permission
from oozie.decorators import check_document_access_permission


LOG = logging.getLogger(__name__)


@check_document_access_permission()
@api_error_handler
def create_session(request):
  response = {'status': -1}

  snippet = json.loads(request.POST.get('snippet', '{}'))

  response['session'] = get_api(request.user, snippet).create_session(lang=snippet['type'])
  response['status'] = 0

  return JsonResponse(response)


@check_document_access_permission()
@api_error_handler
def execute(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))

  response['handle'] = get_api(request.user, snippet).execute(notebook, snippet)
  response['status'] = 0

  return JsonResponse(response)


@check_document_access_permission()
@api_error_handler
def check_status(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))

  response['query_status'] = get_api(request.user, snippet).check_status(notebook, snippet)
  response['status'] = 0

  return JsonResponse(response)


@check_document_access_permission()
@api_error_handler
def fetch_result_data(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))
  rows = json.loads(request.POST.get('rows', 100))
  start_over = json.loads(request.POST.get('startOver', False))

  response['result'] = get_api(request.user, snippet).fetch_result(notebook, snippet, rows, start_over)
  response['status'] = 0

  return JsonResponse(response)


@check_document_access_permission()
@api_error_handler
def fetch_result_metadata(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))

  response['result'] = get_api(request.user, snippet).fetch_result_metadata(notebook, snippet)
  response['status'] = 0

  return JsonResponse(response)


@check_document_access_permission()
@api_error_handler
def cancel_statement(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))

  response['result'] = get_api(request.user, snippet).cancel(notebook, snippet)
  response['status'] = 0

  return JsonResponse(response)


@check_document_access_permission()
@api_error_handler
def get_logs(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))

  db = get_api(request.user, snippet)
  response['logs'] = db.get_log(snippet)
  response['progress'] = db._progress(snippet, response['logs']) if snippet['status'] != 'available' else 100
  response['job_urls'] = [{
      'name': job,
      'url': reverse('jobbrowser.views.single_job', kwargs={'job': job})
    } for job in db._get_jobs(response['logs'])]
  response['status'] = 0

  return JsonResponse(response)


@check_document_modify_permission()
def save_notebook(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))

  if notebook.get('id'):
    notebook_doc = Document2.objects.get(id=notebook['id'])
  else:
    notebook_doc = Document2.objects.create(name=notebook['name'], type='notebook', owner=request.user)
    Document.objects.link(notebook_doc, owner=notebook_doc.owner, name=notebook_doc.name, description=notebook_doc.description, extra='notebook')

  notebook_doc.update_data(notebook)
  notebook_doc.name = notebook['name']
  notebook_doc.description = notebook['description']
  notebook_doc.save()

  response['status'] = 0
  response['id'] = notebook_doc.id
  response['message'] = _('Notebook saved !')

  return JsonResponse(response)


@check_document_access_permission()
def open_notebook(request):
  response = {'status': -1}

  notebook_id = request.GET.get('notebook')
  notebook = Notebook(document=Document2.objects.get(id=notebook_id))

  response['status'] = 0
  response['notebook'] = notebook.get_json()
  response['message'] = _('Notebook saved !')

  return JsonResponse(response)


@check_document_access_permission()
def close_notebook(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))

  response['status'] = 0
  for snippet in notebook['snippets']:
    try:
      if snippet['result']['handle']:
        get_api(request.user, snippet).close(snippet)
    except QueryExpired:
      pass
  response['message'] = _('Notebook closed !')

  return JsonResponse(response)


@check_document_access_permission()
def close_statement(request):
  response = {'status': -1}

  notebook = json.loads(request.POST.get('notebook', '{}'))
  snippet = json.loads(request.POST.get('snippet', '{}'))

  try:
    response['result'] = get_api(request.user, snippet).close(snippet)
  except QueryExpired:
    pass
  response['status'] = 0

  return JsonResponse(response)
