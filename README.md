# fut-tools-data

Classement des meilleures évolutions EA FC 27, recalculé automatiquement quand
les évolutions actives changent (GitHub Actions, `.github/workflows/evolutions.yml`).

- `scripts/evo_engine.py` : moteur d'évolutions (règles vérifiées sur les parcours FUT.GG) ;
- `scripts/gg_pos_model.py` : estimation du GG Rating par poste (styles de chimie compris), pour présélectionner ;
- `scripts/gen_headless.py` : GG exact via le générateur de l'Evo Lab FUT.GG ;
- `scripts/evo_auto.py` : enchaîne le tout ;
- `data/evo_best.json` : résultat lu par le panneau fut-tools.
