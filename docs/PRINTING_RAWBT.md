# Impression Android avec RawBT — POS-58MINI

Dans la caisse, ouvrir le reçu d'une commande puis toucher **Imprimer avec RawBT**.
Configurer auparavant l'imprimante Bluetooth dans RawBT et réussir son test.
Le ticket est prévu pour du papier 58 mm, zone utile 48 mm / 384 points,
police A (32 caractères par ligne). Les noms longs sont renvoyés à la ligne.
Les accents sont translittérés sur le ticket Bluetooth pour éviter les différences
de pages de caractères entre imprimantes compatibles. L'aperçu conserve les accents.

Le reçu utilise les mêmes permissions, données et soldes que la caisse, y compris
paiements partiels, crédits, retours et remboursements. Les données du ticket sont
transmises localement à RawBT par un intent Android contenant des octets ESC/POS
encodés en base64. Aucun lien public ni cookie de connexion n'est envoyé à RawBT.
Source du protocole : https://github.com/402d/rawbt.402d.ru/blob/master/app2/index.php

L'impression classique / PDF reste accessible, notamment sur ordinateur.
Aucun envoi automatique au chargement, aucune modification du paiement ou du stock,
aucun statut « imprimé » : le navigateur ne reçoit pas de confirmation matérielle.
En cas d'échec, vérifier RawBT et Bluetooth avant une nouvelle tentative.

Déploiement : récupérer le commit dans le dépôt utilisé par PythonAnywhere, puis
recharger l'application dans l'onglet Web. Pas de migration ni nouvelle dépendance.
Tester sur Android une commande impayée puis un paiement partiel, des noms longs
et une réimpression ; vérifier les montants, l'absence de coupure et les accents
translittérés sur le papier. Le test physique final exige le téléphone et l'imprimante.
