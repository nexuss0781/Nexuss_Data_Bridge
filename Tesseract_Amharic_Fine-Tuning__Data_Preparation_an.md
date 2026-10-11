# Tesseract Amharic Fine-Tuning: Data Preparation and Training Findings

The recommended starting point for Amharic adaptation is fine-tuning the official `tessdata_best/amh.traineddata` model with the current `tesstrain` workflow. The core preparation work is not collecting more plain Amharic text: it is producing correctly aligned, line-level image/transcription pairs that reflect the documents the model will see in use. Tesseract’s training guide also cautions that retraining is unlikely to help when the actual issue is input-image quality or page segmentation, so a baseline and preprocessing check should come first. [1] [2] [6]

## What the training data must contain

The current `tesstrain` workflow expects a directory of text-line images and a separate, exact plain-text transcription for each image. Supported image formats are TIFF (`.tif`) and PNG (`.png`, `.bin.png`, or `.nrm.png`). The transcription must be single-line text and share the image’s base name, with the image extension replaced by `.gt.txt`. For example:

```text
data/amh_domain-ground-truth/book03_p014_l002.png
data/amh_domain-ground-truth/book03_p014_l002.gt.txt
```

Tesseract’s LSTM training data is built from image-plus-UTF-8-transcription examples. The training guide describes line-level supervision as sufficient for LSTM training; the current workflow automates preparation from the paired images and `.gt.txt` files. Page-level text without alignment to the corresponding line image is not enough. If starting from whole pages, first segment them into text lines and make sure each crop is matched to its own corrected transcription. [1] [2]

Keep a source manifest outside the training directory, recording at least the source document or scan, page, line identifier, and any useful domain labels such as print type or acquisition source. This preserves provenance and makes it possible to audit mislabeled lines, produce held-out document-level evaluation sets, and understand where errors occur. The sample names above are illustrative; the essential requirement is unique, matching image and text basenames. [2] [9]

## Amharic-specific coverage and text conventions

Amharic is written in the Ethiopic script. W3C’s Ethiopic resources describe the script as left-to-right and note that Amharic uses a large syllabic character inventory, native punctuation and digits, and both ordinary spaces and, in some modern text, the Ethiopic wordspace character. The training corpus should therefore be designed around the actual target material: retain the punctuation, word boundaries, digits, and any Latin text that appear in the target documents rather than assuming that a generic Amharic word list represents the full OCR task. [8]

Make the transcription policy explicit and consistent before annotation starts. In particular, decide how the corpus will represent Ethiopic versus Western digits, Ethiopic wordspace versus ordinary space, punctuation, abbreviations, and mixed Ethiopic/Latin lines. Transcribe what is visibly present according to that policy; do not silently rewrite spelling or expand abbreviations. Check that image crops do not clip marks or punctuation and do not include fragments from adjacent lines.

Store labels as Unicode text and standardize the encoding and normalization policy across all annotations. Unicode guidance explains that NFC is a canonical normalization form, while compatibility normalization such as NFKC can erase distinctions and should not be applied blindly to arbitrary text. For OCR ground truth, the practical rule is to use a consistent policy, retain a copy of the original transcription, and ensure evaluation applies the same policy to both recognized and reference text. [7] [9]

## Langdata is useful, but it is not the image corpus

Tesseract’s `langdata` repository is a source of language-specific training resources. The Amharic directory currently lists `amh.training_text`, `amh.wordlist`, `amh.word.bigrams`, `amh.punc`, unigram and bigram frequency files, and `amh.numbers`. These files can support character-set and language-model preparation, but they are not a collection of labeled image/transcription pairs. Tesseract’s langdata project separately notes that reproducing language training also requires script-level data and fonts for rendering. [3] [4]

One current-state detail is worth correcting in the earlier findings: the Amharic `amh.numbers` file is present in the repository listing, but the GitHub API reports its size as **0 bytes** at the current `main` branch snapshot checked on 11 October 2026. So it is safer to describe the file as present but empty, not as a populated set of Amharic number patterns. If number recognition matters, include representative numbers in the labeled line images and test set; if a number-pattern DAWG is needed, supply and validate a populated custom file rather than assuming the stock file provides one. [3] [2]

The byte sizes previously cited for `amh.traineddata`—5,470,094 bytes (`tessdata_fast`), 8,389,639 bytes (`tessdata_best`), and 8,423,467 bytes (`tessdata`)—refer to compiled model files, not to a labeled image corpus or its training volume. Tesseract’s model documentation distinguishes the model repositories by engine/model format and identifies `tessdata_best` as the only official set suitable as a base for fine-tuning. `tessdata_fast` is integerized and not retrainable; `tessdata` includes legacy support and integerized LSTM models. Use the best model as the initialization checkpoint, not the fastest file merely because it is smaller or installed by default. [5]

## Build a corpus that matches deployment

Prioritize representative, accurately labeled material over an arbitrary line count. Tesseract’s training documentation says models benefit from more training text and pages, but also emphasizes that training material should resemble the material expected at recognition time. For an Amharic adaptation, that means matching the target’s fonts or typefaces, print quality, line size, scan or camera characteristics, vocabulary, punctuation, and mix of scripts. Keep enough variation to avoid teaching only one page style, but do not add unrelated data that obscures the intended target domain. [1]

A 2020 Amharic OCR paper describes a 337,337-line-image database combining printed material and synthetic examples from Power Geez and Visual Geez fonts, with degradation variants. It is a useful example of separating data by image source and font and of including both real print and synthetic variation. The paper’s reported dataset is not proof that it is currently accessible or licensed for a particular project; verify availability and reuse terms before incorporating it. Synthetic data can supplement real examples, but should not replace a representative held-out evaluation set from the intended document sources. [11]

For scanned pages, retain the original page image and create line crops from it. Use a repeatable segmentation process, then visually inspect the crops. Tesseract’s image-quality guidance recommends checking deskew, resolution, thresholding, noise, borders, and crop tightness; it gives 300 dpi as a useful resolution target for input documents and warns that skew and unsuitable borders can impair recognition. Apply only preprocessing that is compatible with the planned inference pipeline, and preserve a raw image so preprocessing changes remain reproducible. [6]

## Transcription and dataset quality control

A low-quality label can actively train the recognizer toward the wrong output. Review every transcription against its crop, with additional attention to visually confusable Ethiopic characters, word boundaries, punctuation, digits, and mixed-script content. A practical quality pass should also verify that every image has exactly one matching `.gt.txt` file, each transcript is a single line, filenames are unique, and there are no corrupt or empty images. Keep notes about uncertain readings and exclude unresolved samples rather than turning guesses into ground truth. [1] [2]

Split data before training with evaluation in mind. `tesstrain` defaults to a 90/10 train/evaluation ratio and exposes `RATIO_TRAIN` and `RANDOM_SEED`. That default describes a file-level split; it does not promise that related lines from the same page, volume, scan session, or font will stay together. If the goal is to estimate performance on unseen documents, reserve entire documents or source groups for evaluation. Group-based splitting is specifically designed to keep samples with the same group identifier together. Keep a further untouched test set for the final comparison when possible. [2] [10]

Do not use the final test set to choose checkpoints or tune preprocessing. Evaluate the original Amharic model and the adapted model on exactly the same held-out examples. OCR evaluation guidance recommends realistic ground truth and reporting that can localize errors across documents or document classes, rather than relying on a single aggregate score. [9]

## Fine-tuning workflow

The current `tesstrain` README requires Tesseract **5.3 or later** built with the training tools, matching Leptonica bindings, GNU Make 4.2 or later, `wget`, `find`, `bash`, `unzip`, and Python 3 with Pillow. Ensure the training utilities are installed and accessible before starting; the official build guidance lists ICU, Pango, and Cairo as dependencies for building the training tools from source. [2] [12]

Before adapting the model, run the existing `amh` model on a representative sample and record the result. Check input preprocessing and page segmentation first. Tesseract’s image-quality guide identifies these as common causes of poor recognition and explicitly advises against retraining as a first response when the model is already appropriate for the text. This baseline also gives a fair same-data comparison after fine-tuning. [6]

A typical fine-tuning command is:

```bash
make training \
  MODEL_NAME=amh_domain_v1 \
  START_MODEL=amh \
  TESSDATA=/path/to/tessdata_best \
  GROUND_TRUTH_DIR=data/amh_domain_v1-ground-truth \
  RATIO_TRAIN=0.90 \
  RANDOM_SEED=17 \
  MAX_ITERATIONS=10000
```

`START_MODEL=amh` selects the model to continue from, while `TESSDATA` must point to the directory containing `amh.traineddata` from `tessdata_best`. Use a distinct `MODEL_NAME` for the adapted result. The `tesstrain` README lists a default fine-tuning learning rate of `0.0001` when `START_MODEL` is set and a default maximum of 10,000 iterations; treat these as workflow defaults, not guaranteed optimal settings. Begin with the default fine-tuning rate, retain the random seed, and let evaluation results guide changes. If you need document-grouped train/evaluation splits, do not assume the example’s default random line split achieves that; configure or prepare the lists accordingly and reserve an independent group-held-out test set. [2]

During training, use the workflow’s `evaluation` target and CER plots to compare checkpoints on the evaluation data. Prefer checkpoints that improve held-out error, not just training error. `make traineddata` can convert checkpoints into `tessdata_best` (double-based) and `tessdata_fast` (integer-based) model files. Test the selected checkpoint against the original model on the untouched test set before deployment. The generated model may not include a dictionary by default; if the tool reports that warning, treat dictionary/DAWG resources as a separate configuration decision from image-label training and validate their effect on the target text. [2]

CER is a character-level edit-distance measure based on insertions, deletions, and substitutions; WER applies the analogous comparison at the word level. Report both where word boundaries matter, and inspect results by source or document type so gains on clean print do not conceal regressions on degraded scans or mixed-script lines. Use the same transcription normalization, whitespace handling, and aggregation rules for every model comparison. [9]

## Practical preparation checklist

- Use paired Amharic line crops and exact UTF-8 `.gt.txt` labels; do not treat langdata or a page-only transcript as the supervised image set. [1] [2] [4]
- Define spelling, spaces, punctuation, Ethiopic/Western digits, and mixed-script conventions before annotation. Keep normalization consistent and avoid indiscriminate compatibility normalization. [7] [8]
- Preserve crop quality and match training preprocessing to the intended recognition pipeline. [6]
- Record source/page metadata, and hold out whole documents or sources when evaluating generalization. [9] [10]
- Fine-tune from `tessdata_best/amh.traineddata` with `tesstrain`; compare the result against the original model on the same untouched test set. [2] [5]
- Remember that the current stock `amh.numbers` file is empty; include target number forms in labeled examples and evaluate them explicitly. [3]

## References

[1]: https://tesseract-ocr.github.io/tessdoc/tess5/TrainingTesseract-5.html "Training Tesseract 5"
[2]: https://github.com/tesseract-ocr/tesstrain "Tesseract tesstrain workflow README"
[3]: https://api.github.com/repos/tesseract-ocr/langdata/contents/amh "Current Amharic langdata directory listing"
[4]: https://github.com/tesseract-ocr/langdata "Tesseract langdata repository README"
[5]: https://tesseract-ocr.github.io/tessdoc/Data-Files.html "Tesseract traineddata files"
[6]: https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html "Improving the quality of Tesseract output"
[7]: https://unicode.org/reports/tr15/ "Unicode Standard Annex #15: Unicode Normalization Forms"
[8]: https://www.w3.org/International/elreq/ethi/ "W3C Ethiopic Script Resources"
[9]: https://ocr-d.de/en/spec/ocrd_eval.html "OCR-D Quality Assurance and Evaluation"
[10]: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupShuffleSplit.html "scikit-learn GroupShuffleSplit"
[11]: https://www.mdpi.com/2076-3417/10/3/1117 "Amharic OCR: An End-to-End Learning"
[12]: https://tesseract-ocr.github.io/tessdoc/Compiling.html "Tesseract compilation and training-tool dependencies"
