# Design System — Bar Manager Pro

## Objectif

Ce Design System fournit un socle visuel partagé pour l'application Flask/Jinja2 existante sans modifier les contrats métier, les routes, les formulaires ni les classes nécessaires au JavaScript.

Le fichier principal est `app/static/design-system.css`. Il est chargé après les styles fonctionnels afin de fournir une couche de compatibilité et de permettre une migration progressive.

## Tokens

Les tokens sont préfixés par `--ds-` :

- couleurs de marque, texte, surfaces et états ;
- espacements ;
- rayons ;
- ombres ;
- effets glassmorphism ;
- typographie ;
- transitions ;
- z-index ;
- dimensions de navigation.

Les anciens alias (`--brand`, `--brand-dark`, `--brand-soft`, `--accent`, `--ink`, `--muted`, `--line`, `--surface`, `--page`) restent disponibles afin de ne pas casser les feuilles CSS existantes.

## Thème sombre

Le thème sombre est prévu sans l'imposer aux utilisateurs existants.

L'activer sur un conteneur racine avec :

```html
<html data-theme="dark">
```

ou avec l'attribut Bootstrap :

```html
<html data-bs-theme="dark">
```

Les nouveaux composants doivent utiliser les tokens plutôt que des couleurs codées en dur pour rester compatibles avec les deux thèmes.

## Glassmorphism

Classes disponibles :

- `.ds-glass`
- `.glass-card`
- `.glass-panel`
- `.glass-subtle`
- `.glass-strong`

Le rendu reste opaque et lisible si `backdrop-filter` n'est pas pris en charge. Les tableaux, formulaires et montants ne dépendent pas du flou pour leur lisibilité.

## Composants

### Layout

- `.ds-stack`
- `.ds-cluster`
- `.ds-grid`
- `.ds-section`
- `.ds-surface`
- `.ds-surface-muted`

### KPI

- `.kpi-grid`
- `.kpi-card`
- `.kpi-card-label`
- `.kpi-card-value`
- `.kpi-card-helper`

### Statuts

- `.status-badge`
- `.status-success`
- `.status-warning`
- `.status-danger`
- `.status-info`
- `.status-neutral`

Les classes historiques `.status-pill`, `.status-active` et `.status-suspended` sont conservées.

### Filtres

Utiliser `.filter-panel` ou `.ds-filter-panel` autour des contrôles de filtre.

### Notifications

Utiliser `.ds-notice` pour les messages applicatifs qui ne correspondent pas directement aux variantes Bootstrap `.alert-*`.

### États vides et chargement

- `.ds-empty-state`
- `.ds-empty-icon`
- `.ds-loading`
- `.ds-spinner`
- `.ds-skeleton`

### Dialogues

La classe `.ds-dialog` harmonise les dialogues HTML natifs, y compris le backdrop. Les modales Bootstrap et offcanvas héritent également des tokens.

## Bootstrap

Le Design System redéfinit uniquement les variables et composants visuels compatibles avec Bootstrap 5.3.3. Il ne change pas les attributs `data-bs-*`, les contrats JS ou les noms de champs des formulaires.

## Responsive

Le socle est mobile-first et utilise principalement :

- `clamp()` pour les tailles fluides ;
- `minmax()` et `auto-fit` pour les grilles ;
- Flexbox avec retour à la ligne ;
- cibles tactiles de 44 px minimum ;
- largeur de contenu fluide avec plafond à 1600 px.

Vérifications manuelles recommandées : 320, 360, 375, 390, 428, 768, 1024, 1280, 1366, 1440 et 1920 px.

## Accessibilité

- focus visible renforcé ;
- respect de `prefers-reduced-motion` ;
- adaptation à `prefers-contrast: more` ;
- conservation du skip-link existant ;
- pas de dépendance au flou pour comprendre une information ;
- contrastes sémantiques séparés des seules variations de transparence.

## Migration progressive

Lorsqu'une page est modernisée :

1. conserver les classes et attributs utilisés par le JavaScript ;
2. remplacer progressivement les couleurs codées en dur par des tokens `--ds-*` ;
3. réutiliser les composants du Design System avant de créer une variante locale ;
4. tester au minimum mobile, tablette et desktop ;
5. conserver les champs, noms, méthodes, endpoints et CSRF des formulaires Jinja2.
