# GPU memory for a self-hosted model

A model served on the machine's own GPUs has to fit in their memory. This
page says how much memory Qwen3.6-27B needs in its two versions, where that
memory goes, and how the need grows with the benchmark's domain and with
the number of attempts run at once. The figures are estimates worked out
from the model's published sizes; the last section says which of them have
been checked against a running server.

Sizes are in MiB, the unit `nvidia-smi` prints. A card sold as 48 GB shows
46,068 MiB.

## The short answer

| version of the model                                | model file        |   memory needed (MiB) | fits on                                                       |
|-----------------------------------------------------|-------------------|-----------------------|---------------------------------------------------------------|
| 8-bit numbers (`Qwen/Qwen3.6-27B-FP8`), the default | `qwen3.6-27b-fp8` |                40,692 | one 48 GB card with nothing else on it, with little to spare  |
| as released, 16-bit numbers (`Qwen/Qwen3.6-27B`)    | `qwen3.6-27b`     |                66,192 | two 48 GB cards with nothing else on them, 33,096 MiB on each |

That is for one prompt at a time of up to 131,072 tokens, the limit both
model files set. Several attempts at once need more, as shown further down.

## The two versions

The 8-bit version, called FP8, is the one the examples and the experiments
here use, because it fits a single card. It is published by the model's
authors as `Qwen/Qwen3.6-27B-FP8`: the same model with each weight stored
as an 8-bit floating-point number and not a 16-bit one, which halves the
files from 55.6 GB to 30.9 GB. Its model card calls its results "nearly
identical to those of the original model" and gives no figures; the
difference on this benchmark has not been measured here.

Three things follow:

- **It is a different model.** Results on it are reported under its own
  name, and a published result for Qwen3.6-27B is not a result for it.
- **The prompt cache is the same size.** The layers and the numbers the
  server computes with are unchanged, so only the weights' row differs in
  the table below.
- **It asks more of the card.** The 8-bit arithmetic needs a card of
  NVIDIA's Ada Lovelace generation or newer, such as an L40. On an older
  card vLLM runs the model with the weights alone in 8 bits.

The two model files differ in the model, the port and the number of cards,
and in nothing else.

## Where the memory goes

| part             | what it is                                                                                                              |              8-bit (MiB) |        as released (MiB) |
|------------------|-------------------------------------------------------------------------------------------------------------------------|--------------------------|--------------------------|
| the weights      | the model's own numbers, loaded once                                                                                    |                   29,500 |                   53,000 |
| the prompt cache | what the server keeps of each prompt it has read, so that writing the next token does not mean reading the prompt again | 8,192 for 131,072 tokens | 8,192 for 131,072 tokens |
| working memory   | the space the server computes in                                                                                        |     3,000 (an allowance) |     5,000 (an allowance) |
| total            |                                                                                                                         |                   40,692 |                   66,192 |

The weights are the sizes of the model's files on Hugging Face. vLLM calls
the prompt cache the KV cache.

### The prompt cache, token by token

A layer of the model that looks back over the whole prompt keeps, for every
token, a key and a value for each of its key-value heads:

```
bytes a token = 2 x layers that keep a cache x key-value heads x head size x bytes a number
```

The model's `config.json` gives the terms. Qwen3.6-27B has 64 layers, of
which every fourth looks back over the whole prompt (`full_attention_interval:
4`), so 16 keep a cache; each has 4 key-value heads (`num_key_value_heads`)
of size 256 (`head_dim`); and a number takes 2 bytes:

```
2 x 16 x 4 x 256 x 2 = 65,536 bytes = 64 KiB a token
```

A prompt of 131,072 tokens therefore takes 8,192 MiB. The other 48
layers keep a state of one size for each prompt, however long it is; that
state is left out here.

The formula was checked on a smaller model of the same family, Qwen3-0.6B
(28 layers that all keep a cache, 8 key-value heads of size 128): it gives
112 KiB a token, and vLLM 0.34.0, refusing a limit of 16,384 tokens on a
small card, said that limit needed 1.75 GiB, which is 112 KiB a token.

## How the need grows with the domain

An attempt is one run of the agent on one task, and it holds its prompt in
the cache while it runs. How long the prompt is depends on the domain and on the tools the agent is given: every
tool the domain's server offers, which is the setting of the experiments,
or only the tools the benchmark lists with the task.

| tools given              | domain   |   prompt, median (tokens) |   prompt, 95th percentile (tokens) |   cache for one attempt, median (MiB) |   cache for one attempt, 95th percentile (MiB) |
|--------------------------|----------|---------------------------|------------------------------------|---------------------------------------|------------------------------------------------|
| every tool of the domain | CSM      |                    34,021 |                             57,738 |                                 2,126 |                                          3,609 |
| every tool of the domain | HR       |                    45,474 |                             50,817 |                                 2,842 |                                          3,176 |
| every tool of the domain | ITSM     |                    51,274 |                             65,173 |                                 3,205 |                                          4,073 |
| the task's own           | CSM      |                    10,128 |                             31,031 |                                   633 |                                          1,939 |
| the task's own           | HR       |                    10,964 |                             18,751 |                                   685 |                                          1,172 |
| the task's own           | ITSM     |                    11,911 |                             22,513 |                                   744 |                                          1,407 |

A prompt is what one call to the model sends: the tool descriptions, the
policy, the task and the conversation so far. The lengths are each
attempt's mean over its calls; its last prompt is longer than its mean, so
its need at the end is somewhat above these. The cache columns are the
lengths times 64 KiB.

The lengths come from runs of the benchmark's own agent on Claude Haiku 5.5
and are counted by that model's tokeniser: the task's own tools on every
task of the three domains, three attempts each, and every tool on 125 of
the tasks, three attempts each (`docs/data/prompt-sizes.csv`). Qwen counts
tokens differently, so they are a guide until a run on Qwen replaces them.

## How the need grows with attempts at once

The runner keeps several attempts in flight (`concurrency` in the experiment
file, 4 unless changed), and each holds its own prompt:

```
prompt cache = the larger of  one prompt of the longest length allowed
                              attempts at once x the cache for one attempt
memory       = weights + working memory + prompt cache
```

With every tool given and the 95th-percentile prompt of each domain:

| domain   |   attempts at once |   prompt cache (MiB) |   total, 8-bit (MiB) |   total, as released (MiB) |
|----------|--------------------|----------------------|----------------------|----------------------------|
| CSM      |                  1 |                8,192 |               40,692 |                     66,192 |
| CSM      |                  2 |                8,192 |               40,692 |                     66,192 |
| CSM      |                  4 |               14,434 |               46,934 |                     72,434 |
| CSM      |                  8 |               28,869 |               61,369 |                     86,869 |
| HR       |                  1 |                8,192 |               40,692 |                     66,192 |
| HR       |                  2 |                8,192 |               40,692 |                     66,192 |
| HR       |                  4 |               12,704 |               45,204 |                     70,704 |
| HR       |                  8 |               25,408 |               57,908 |                     83,408 |
| ITSM     |                  1 |                8,192 |               40,692 |                     66,192 |
| ITSM     |                  2 |                8,192 |               40,692 |                     66,192 |
| ITSM     |                  4 |               16,293 |               48,793 |                     74,293 |
| ITSM     |                  8 |               32,586 |               65,086 |                     90,586 |

One 48 GB card holds 46,068 MiB, and vLLM 0.34.0 takes all the memory it
finds free on each card it is given. By these figures the 8-bit version has
room on one card for two attempts at once in every domain, and for four in
HR only. The released model has room on two cards (92,136 MiB) for
four attempts at once in every domain; eight are at the edge, at
83,408 to 90,586 MiB before the parts this estimate leaves out.

More attempts in flight than the cache holds is not an error: the server
makes the extra requests wait their turn, which costs time and fails
nothing. On a small model, four prompts of 9,021 tokens sent at once to a
cache of 14,848 tokens were all answered, in 2 to 7 seconds against 1.7 for
one alone, and the server's log showed requests waiting. So a `concurrency`
above what fits is safe and gains no speed.

The table counts every attempt's whole prompt. vLLM can keep once the start
that several prompts share, here the tool descriptions (its prefix
caching); what that saves has not been measured.

## Reading the real figures from a start

The server writes what it found to its log, whose path `mas-optimiser
server` prints. Four lines carry the figures above, shown here as vLLM
0.34.0 writes them (for a small model on a 6 GB card, with a limit of
12,288 tokens and then of 16,384):

```
Model loading took 1.12 GiB memory and 4.888129 seconds
GPU KV cache size: 14,848 tokens
Maximum concurrency for 12,288 tokens per request: 1.21x
ValueError: To serve at least one request with the model's max seq len (16384), (1.75 GiB KV cache is needed, which is larger than the available KV cache memory (1.58 GiB). Based on the available memory, the estimated maximum model length is 14784.
```

The first is the weights. The second is the prompt cache the server ended
up with, in tokens, and the third how many prompts of the longest length
allowed fit in it at once. The fourth appears only when the limit on prompt
length does not fit, and names the longest that would; `mas-optimiser
server` prints it when it reports that the server stopped by itself.

## Fitting other cards

The model file's `server` block holds the settings (`configs/models/qwen3.6-27b-fp8.yaml`).

| the machine has                  | what to change                                                                                                                                                              |
|----------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| a different number of cards      | `--tensor-parallel-size`, the number of cards the model is spread over                                                                                                      |
| less memory than the tables ask  | a lower `--max-model-len` (every 16,000 tokens fewer free 1,000 MiB) or fewer attempts at once (`concurrency` in the experiment file)                                       |
| two cards of 48 GB to itself     | the model as released: `qwen3.6-27b` in place of `qwen3.6-27b-fp8`                                                                                                          |
| cards shared with other programs | `mas-optimiser server <model> --gpus 2,3` to name the cards, and `--gpu-memory-utilization=<share>` among the server's options to leave the rest of each card to the others |

Before a start, `mas-optimiser server` warns of any card that already has
memory in use, since that is the usual reason for a model not to fit.

## What has been checked

| figure                                      | where it stands                                                                                                |
|---------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| the weights                                 | the sizes of the model's files on Hugging Face, read on 23 and 24/11/2026                                      |
| the prompt cache a token                    | from the formula; the formula gives vLLM's own figure for Qwen3-0.6B, and has not been compared on Qwen3.6-27B |
| the 48 layers left out of the formula       | not worked out; they make the real figure larger than the estimate by an amount not known                      |
| working memory                              | an allowance, not a measurement                                                                                |
| prompt lengths                              | counted by another model's tokeniser, on that model's runs                                                     |
| the 8-bit version                           | its file follows its model card's commands for vLLM; it has not yet been served here                           |
| more prompts in flight than the cache holds | observed on a small model: every request was answered, the extra ones after a wait                             |
| the totals                                  | not yet confirmed by a start of either version on cards with nothing else on them                              |
