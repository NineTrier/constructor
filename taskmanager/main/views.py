from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from document.models import DocumentsPattern, DocType, Document_ParentDocument
from user_manager.models import Profile


@login_required
def index(request):
    profile = Profile.for_user(request.user)
    colleagues = Profile.objects.filter(organisation=profile.organisation).exclude(pk=profile.pk)
    docs_of_all = DocumentsPattern.objects.filter(owner__in=colleagues)

    documents_by_type = {}
    for doc_type in DocType.objects.all().order_by('name'):
        docs_queryset = DocumentsPattern.objects.filter(type=doc_type).filter(
            Q(documentOfOrganisation=True) | Q(owner=profile)
        ).order_by('-documentOfOrganisation', '-lastUpdate')

        items = []
        for document in docs_queryset:
            parent_link = Document_ParentDocument.objects.filter(document=document).select_related('parent__owner').first()
            owner = parent_link.parent.owner if parent_link else document.owner
            items.append({document: owner})
        documents_by_type[doc_type] = items

    context = {
        'documentsOfUser': DocumentsPattern.objects.filter(owner=profile, documentOfOrganisation=False).order_by('-lastUpdate'),
        'documentsOfOrganisation': DocumentsPattern.objects.filter(documentOfOrganisation=True).order_by('-lastUpdate'),
        'documentOfAll': docs_of_all.order_by('-lastUpdate'),
        'documents': documents_by_type,
        'noimage': f"{settings.MEDIA_ROOT}/noimage.jpeg",
    }
    return render(request, 'main/index.html', context)


def about(request):
    return render(request, 'main/about.html')


def teliki(request, pk):
    return render(request, 'main/teliki.html', context={'target': pk})


def match_machine(request):
    return render(request, 'main/match_machine.html')
