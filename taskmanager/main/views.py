from django.shortcuts import render, redirect
from document.models import Documents
from user_manager.models import Profile
from django.conf import settings


# Главная страница веб-сервиса
def index(request):
    if request.user.is_authenticated:
        profileUser = Profile.objects.filter(user=request.user)[0]
        docsQuery = []
        for human in Profile.objects.filter(organisation=profileUser.organisation):
            if human == profileUser:
                continue
            docsQuery.append(Documents.objects.filter(owner=human))
        if len(docsQuery) > 0:
            docsOfAllInOrganisation = docsQuery[0]
            for query in docsQuery:
                docsOfAllInOrganisation.union(query)
        else:
            docsOfAllInOrganisation = []
        
        context = {
            'title': 'Главная страница сайта',
            'documentsOfUser': Documents.objects.filter(owner=profileUser).filter(documentOfOrganisation=False).order_by('-lastUpdate'),
            'documentsOfOrganisation': Documents.objects.filter(documentOfOrganisation=True).order_by('-lastUpdate'),
            'documentOfAll': docsOfAllInOrganisation.order_by('-lastUpdate'),
            'noimage': f"{settings.MEDIA_ROOT}/noimage.jpeg"
        }
        if request.user.is_authenticated:
            context['profile'] = profileUser
            return render(request, 'main/index.html', context)
    else:
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
