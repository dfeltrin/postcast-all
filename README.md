# Podcast All

Agente locale per creare Audio Overview in NotebookLM a partire da URL o file
locali, mantenendo una coda persistente. Un elemento viene rimosso dalla coda
solo dopo il download riuscito dell'audio.

- Per un URL crea MP3, TXT (URL della fonte) e Markdown (testo estratto).
- Per un file locale crea MP3 e TXT (percorso assoluto del file); non crea Markdown.

## Prerequisiti

- Python 3.10+
- Un account Google con accesso a [NotebookLM](https://notebooklm.google.com/)
- Il diritto di riprodurre/elaborare la fonte indicata

## Installazione

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
```

## Avvio

```bash
./go.sh
```

In alternativa, dopo l'installazione:

```bash
news-to-audio
```

Al primo avvio si apre Chromium: accedi a Google **manualmente**. La sessione è
conservata in `state/browser-profile`, quindi gli avvii successivi normalmente
non richiedono un nuovo accesso.

## Coda da terminale

Comandi principali:

- `/add <url>` oppure `/a <url>`: aggiunge un URL alla coda.
- `/addfile` oppure `/af`: apre il selettore di sistema e aggiunge un file locale.
- `/list` oppure `/l`: mostra URL e file in attesa.
- `/sync` oppure `/s`: elabora la coda nell'ordine di inserimento.
- `/removeall` oppure `/r`: svuota completamente la coda.
- `/rejected`: mostra gli URL scartati automaticamente.
- `/output [cartella]` oppure `/o [cartella]`: mostra o imposta la cartella di output.

Con `/addfile` il file viene memorizzato in coda insieme agli URL, non inviato
subito a NotebookLM. Durante `/sync`, il file viene caricato solo quando arriva
il suo turno. L'app attende il completamento del caricamento e dell'analisi
(spinner incluso) prima di avviare l'Audio Overview.

Se il file viene spostato o eliminato prima dell'elaborazione, rimane in coda e
la sincronizzazione si ferma senza ritentare in loop. Dopo aver corretto il
problema, esegui di nuovo `/sync`: l'app riprende il notebook già creato.

## Formati

Per gli URL gestisce HTML, PDF, TXT/MD, DOCX, PPTX e CSV scaricabili. Per i file
locali seleziona un formato supportato dal tuo account NotebookLM. I contenuti
protetti da login/paywall non vengono aggirati.

## Diagnostica

L'interazione con NotebookLM avviene nel browser. In caso di errore vengono
salvati screenshot e HTML in `output/.debug`.
