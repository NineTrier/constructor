from django.shortcuts import render, redirect
from document.models import DocumentsPattern, DocType, Document_ParentDocument
from user_manager.models import Profile
from django.conf import settings


# Главная страница веб-сервиса
def index(request):
    try:
        if request.user.is_authenticated:
            profileUser = Profile.objects.filter(user=request.user)[0]
            docsQuery = []
            for human in Profile.objects.filter(organisation=profileUser.organisation):
                if human == profileUser:
                    continue
                docsQuery.append(DocumentsPattern.objects.filter(owner=human))
            if len(docsQuery) > 0:
                docsOfAllInOrganisation = docsQuery[0]
                for query in docsQuery:
                    docsOfAllInOrganisation.union(query)
            else:
                docsOfAllInOrganisation = DocumentsPattern.objects.none()
            docs = {}
            for typ in DocType.objects.all().order_by('name'):
                docs[typ] = []
                for doc in DocumentsPattern.objects.filter(type=typ).filter(documentOfOrganisation=True).union(DocumentsPattern.objects.filter(type=typ).filter(owner=profileUser)).order_by('-documentOfOrganisation','-lastUpdate'):
                    print()
                    if len(Document_ParentDocument.objects.filter(document=doc)) > 0:
                        parent = Document_ParentDocument.objects.filter(document=doc)[0].parent
                        print(parent)
                        docs[typ].append({doc: parent.owner})
                    else:
                        docs[typ].append({doc: doc.owner})
            print(docs)
            context = {
                'title': 'Главная страница сайта',
                'documentsOfUser': DocumentsPattern.objects.filter(owner=profileUser).filter(documentOfOrganisation=False).order_by('-lastUpdate'),
                'documentsOfOrganisation': DocumentsPattern.objects.filter(documentOfOrganisation=True).order_by('-lastUpdate'),
                'documentOfAll': docsOfAllInOrganisation.order_by('-lastUpdate'),
                'documents': docs,
                'noimage': f"{settings.MEDIA_ROOT}/noimage.jpeg"
            }
            if request.user.is_authenticated:
                context['profile'] = profileUser
                return render(request, 'main/index.html', context)
        else:
            return redirect('/accounts/login')
    except Exception as exc:
        print(exc)
        return redirect('/accounts/login')


# Страница about
def about(request):
    if request.user.is_authenticated:
        profileUser = Profile.objects.filter(user=request.user)[0]
        context = {}
        if request.user.is_authenticated:
            context['profile'] = profileUser
            return render(request, 'main/about.html', context)
    return render(request, 'main/about.html')

def teliki(request, pk):
    return render(request, 'main/teliki.html', context={'target': pk})

def match_machine(request):
    documents = DocumentsPattern.objects.filter(documentOfOrganisation=True).order_by('-lastUpdate')
    return render(request, 'main/match_machine.html')