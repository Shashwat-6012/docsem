# The architecture in one picture

This structure directly represents your conceptual pipeline:

```text
                       USER
                        │
                        ▼
                  ┌──────────┐
                  │  api.py  │
                  │  DocSem  │
                  └────┬─────┘
                       │
                       ▼
                 ┌─────────────┐
                 │  pipeline/  │
                 └──────┬──────┘
                        │
              ┌─────────┴─────────┐
              │                   │
              ▼                   ▼
       ┌────────────┐      ┌──────────────┐
       │ extraction │      │   semantic/  │
       │            │      │              │
       │ normalized │      │ LLM / SLM    │
       │ input      │      │ reasoning    │
       └─────┬──────┘      └───────┬──────┘
             │                     │
             ▼                     │
       ┌────────────┐              │
       │ components │              │
       └─────┬──────┘              │
             │                     │
             ▼                     │
          ┌───────┐                │
          │ zones │                │
          └───┬───┘                │
              │                    │
              ▼                    │
         ┌─────────┐               │
         │ PageIR  │───────────────┘
         └────┬────┘
              │
              ▼
       Semantic Reconstruction
              │
              ▼
        ┌─────────────┐
        │ DocumentIR  │
        └─────────────┘
```


# The resulting mental model

If you remember only one thing, I'd make it this:

```text
┌─────────────────────────────────────┐
│              docsem                 │
│                                     │
│  ┌───────────┐                      │
│  │ Extraction│ ← physical boundary  │
│  └─────┬─────┘                      │
│        │                            │
│  ┌─────▼─────────────┐              │
│  │ Components + Zones│              │
│  └─────┬─────────────┘              │
│        │                            │
│  ┌─────▼─────┐                      │
│  │  PageIR   │                      │
│  └─────┬─────┘                      │
│        │                            │
│  ┌─────▼──────────────────────┐     │
│  │ Semantic Reconstruction    │     │
│  │                            │     │
│  │  SemanticModel             │     │
│  │      │                     │     │
│  │      ├── External LLM      │     │
│  │      └── Local SLM         │     │
│  └─────┬──────────────────────┘     │
│        │                            │
│  ┌─────▼──────┐                     │
│  │DocumentIR  │ ← final artifact    │
│  └────────────┘                     │
│                                     │
└─────────────────────────────────────┘
```
