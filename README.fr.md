# HomeServer Monitoring

Projet MIT séparé pour la supervision : console locale, architecture, sondes,
références d'arrêt/reprise et laboratoire de connexion commune.

**Version expérimentale.** La démo utilise des données fictives. Aucun déploiement
automatique, mot de passe ou paramètre personnel n'est fourni. Le SSO reste un
prototype testé en laboratoire ; sa mise en production n'est pas déclarée terminée.

```sh
python infrastructure/monitoring/console.py --demo
```

Python 3.10+ suffit. Le navigateur ouvre la console sur `127.0.0.1:18765` avec un
jeton local ; toutes les actions sont interdites en démonstration. Hors démo,
les actions restent désactivées par défaut et SSH exige une clé d'hôte vérifiée.

Le [README principal](README.md) détaille la configuration, les tests et les
limites. Les dépôts [HomeServer](https://github.com/Fogbose/HomeServer) et
[HomeServer-Software](https://github.com/Fogbose/HomeServer-Software) restent
indépendants. RAID, veille, WOL et restauration nécessitent leurs propres essais.
