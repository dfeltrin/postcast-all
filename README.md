# Podcast All

Agente locale con due operazioni distinte: avvia un Audio Overview da un URL e,
in un secondo momento, scarica gli audio già pronti dai notebook esistenti.
Per ogni MP3 scaricato viene creato anche un file `.txt` omonimo, contenente
l'URL della fonte originale. I file MP3, TXT e Markdown vengono creati insieme
soltanto dopo il download riuscito dell'audio; solo allora il notebook
viene rinominato con il prefisso `[DONE]` e non verra' scaricato di nuovo.

## Prerequisiti

- Python 3.11+
- Un account Google con accesso a [NotebookLM](https://notebooklm.google.com/)
- Il diritto di riprodurre/elaborare la fonte indicata

## Installazione

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
```

## Uso

```bash
news-to-audio 'https://example.com/articolo' \
  --output-dir ~/Downloads/AudioOverview \
  --language Italian \
  --prompt 'Spiega i concetti chiave in modo chiaro e concreto.'
```

Il comando crea il notebook, importa l'URL e avvia la generazione senza restare
in attesa del risultato. Per scaricare gli Audio Overview disponibili in seguito:

```bash
news-to-audio-download --output-dir ./AudioOverview
```

## Interfaccia grafica

Per avviare l'interfaccia, dopo l'installazione esegui:

```bash
news-to-audio-ui
```

In alternativa, dalla cartella del progetto puoi usare:

```bash
./go.sh
```

Inserisci l'URL e il **titolo obbligatorio**, scegli la cartella di destinazione e premi **Crea audio**.
Il titolo viene usato per il notebook, il file di testo e l'MP3.
L'app aggiunge automaticamente la data corrente nel formato `YYYY.MM.DD` e
rimuove i caratteri non consentiti nei nomi file.
L'app rimane utilizzabile durante l'estrazione e apre NotebookLM al momento
opportuno. Per impostazione predefinita i file vengono salvati in
`./AudioOverview`, rispetto alla cartella dalla quale avvii la UI.

Al primo avvio si apre Chromium: accedi a Google **manualmente**. La sessione e'
conservata in `state/browser-profile`, quindi gli avvii successivi non richiedono
normalmente un nuovo accesso.

L'agente salva anche il testo estratto nella directory di output (`.md`): e' utile
come traccia, per verificare la fonte e per ritentare l'operazione senza scaricarla
di nuovo. Nel flusso NotebookLM l'agente seleziona **Testo copiato** e importa il testo
estratto, preceduto dall'URL originale: questo evita i blocchi dei siti che non consentono
al crawler di NotebookLM di aprire direttamente la pagina.
fornito. Per completare in modo automatico usa il flusso desktop NotebookLM;
l'app mobile conserva invece gli audio solo offline e non come file esportabile.
L'agente attende fino a 200 minuti per la generazione prima di segnalare un timeout.

## Formati

Gestisce HTML, PDF, TXT/MD, DOCX, PPTX e CSV scaricabili via URL. Per gli altri
file o per contenuti protetti da login/paywall, l'esecuzione si interrompe con un
messaggio esplicito: non tenta di aggirare protezioni.

## Limiti operativi

NotebookLM non offre un'API pubblica per creare/scaricare gli Audio Overview.
L'ultimo tratto e' quindi automazione del browser e l'interfaccia di Google puo'
cambiare: i selettori, raccolti in `notebooklm.py`, sono volutamente isolati e
facili da aggiornare. In caso di errore vengono salvati screenshot e HTML in
`output/.debug`.
