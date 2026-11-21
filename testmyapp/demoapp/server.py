"""Webapp d'exemple TestMyApp (§20 : campagne démo sur webapp d'exemple).

Petite boutique mono-fichier, FastAPI. Contient volontairement des défauts
que les personas doivent révéler : page paiement lente (~2,6 s), image
sans alt, checkbox sans label, cible tactile 24 px, contraste insuffisant
sur un bouton. C'est la cible du scénario commander-produit avec le driver
web Playwright.
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

PAGES: dict[str, str] = {}


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; background: #f4f2ee; color: #222; }}
  header {{ background: #22304a; color: #fff; padding: 12px 20px; display: flex; gap: 16px; align-items: center; }}
  header a {{ color: #fff; text-decoration: none; }}
  input[type=search] {{ padding: 8px; width: 260px; }}
  main {{ max-width: 760px; margin: 24px auto; padding: 0 16px; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 12px 0; }}
  .btn {{ padding: 10px 18px; border: 0; border-radius: 6px; cursor: pointer; background: #2264d1; color: #fff; font-size: 15px; }}
  .btn-danger {{ background: #7a2020; color: #a46161; /* contraste volontairement faible */ }}
  .tiny {{ min-width: 48px; height: 24px; font-size: 12px; }}  /* cible tactile < 44px */
  .grid {{ display: flex; gap: 12px; flex-wrap: wrap; }}
</style></head>
<body>
<header>
  <strong>Boutique Démo</strong>
  <a href="/" tabindex="0">Accueil</a><a href="/cart" tabindex="0">Panier (1)</a>
</header>
<main>{body}</main>
</body></html>"""


PAGES["home"] = _page("Boutique Démo — Accueil", """
<h1>Bienvenue à la Boutique Démo</h1>
<div class="card">
  <form action="/results" method="get" role="search">
    <input type="search" name="q" placeholder="Rechercher un produit" aria-label="Rechercher un produit">
    <button class="btn" type="submit">Rechercher</button>
  </form>
</div>
<img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='60'%3E%3Crect width='120' height='60' fill='%23c9d4ea'/%3E%3C/svg%3E">
<!-- image volontairement sans attribut alt -->
""")

PAGES["results"] = _page("Résultats de recherche", """
<h1>Résultats pour « lampe »</h1>
<div class="grid">
  <div class="card">
    <h2>Lampe de bureau « Lumen »</h2><p>39,90 €</p>
    <form action="/product"><button class="btn">Ajouter au panier</button></form>
  </div>
  <div class="card"><h2>Carnet A5 quadrillé</h2><p>4,50 €</p>
    <form action="/product"><button class="btn">Ajouter au panier</button></form></div>
</div>
""")

PAGES["product"] = _page("Lampe de bureau « Lumen »", """
<h1>Lampe de bureau « Lumen » — 39,90 €</h1>
<div class="card">
  <form action="/cart"><button class="btn">Ajouter au panier</button></form>
  <a href="/cart" tabindex="0">Panier (1)</a>
</div>
""")

PAGES["cart"] = _page("Votre panier", """
<h1>Votre panier</h1>
<div class="card">
  <p>1 × Lampe de bureau « Lumen » — 39,90 €</p>
  <form action="/checkout">
    <input type="text" name="promo" placeholder="Code promo" aria-label="Code promo">
    <button class="btn" type="submit" style="min-width:120px">Passer la commande</button>
    <!-- bouton volontairement réduit : cible tactile 24px -->
    <button class="btn tiny" type="button">–</button>
  </form>
</div>
""")

PAGES["checkout"] = _page("Paiement", """
<h1>Paiement</h1>
<div class="card">
  <form action="/confirmation" method="post">
    <p><input type="text" name="card" placeholder="Numéro de carte" aria-label="Numéro de carte" required></p>
    <p><input type="text" name="exp" placeholder="Date d'expiration (MM/AA)" aria-label="Date d'expiration" required></p>
    <p><input type="text" name="cvc" placeholder="CVC" aria-label="CVC" required></p>
    <p><input type="checkbox" name="save"> Enregistrer la carte <!-- case sans label/for --></p>
    <button class="btn btn-danger" type="submit">Payer maintenant</button>
  </form>
</div>
""")

PAGES["confirmation"] = _page("Commande confirmée", """
<h1>Merci pour votre commande !</h1>
<div class="card"><p>Numéro de commande : DEMO-2026-0001</p><a href="/" tabindex="0">Retour à l'accueil</a></div>
""")

def create_demo_app(*, fixed: bool = False) -> FastAPI:
    app = FastAPI(title="Boutique Démo")
    pages = PAGES.copy()
    if fixed:
        for name, content in pages.items():
            pages[name] = content.replace("color: #a46161", "color: #fff").replace("height: 24px", "height: 44px")
        pages["home"] = pages["home"].replace('<img src=', '<img alt="" src=')
        pages["cart"] = pages["cart"].replace('type="button">–', 'type="button" aria-label="Retirer un article">–')
        pages["checkout"] = pages["checkout"].replace(
            '<input type="checkbox" name="save"> Enregistrer la carte',
            '<label><input type="checkbox" name="save"> Enregistrer la carte</label>',
        )

    @app.get("/{page}", response_class=HTMLResponse)
    async def serve(page: str) -> HTMLResponse:
        if page not in pages:
            return HTMLResponse("Page introuvable", status_code=404)
        if page == "checkout" and not fixed:
            await asyncio.sleep(2.6)
        return HTMLResponse(pages[page])

    @app.post("/confirmation", response_class=HTMLResponse)
    async def confirm() -> HTMLResponse:
        return HTMLResponse(pages["confirmation"])

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return HTMLResponse(pages["home"])

    return app


app = create_demo_app()
