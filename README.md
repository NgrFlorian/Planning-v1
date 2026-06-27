# Lancement de l'application

Voici les commandes pour installer les dépendances et lancer l'application directement avec l'environnement virtuel (méthode la plus fiable sur Windows).

## 1. Installer les dépendances (si nécessaire)

Exécutez cette commande dans votre terminal :

```powershell
.venv\Scripts\python.exe -m pip install fastapi uvicorn pydantic
```

## 2. Lancer le serveur de l'application

Lancez l'application en utilisant directement le Python de l'environnement virtuel :

```powershell
.venv\Scripts\python.exe -m uvicorn app:app --reload
```

## 3. Accéder à l'application


Une fois le serveur démarré, ouvrez votre navigateur et accédez à l'adresse suivante :
[http://127.0.0.1:8000](http://127.0.0.1:8000)
