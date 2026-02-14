# Finance-Tuned LLM (LoRA Fine-Tuning) - Task 13

## Status: DEFERRED (Optional Research Task)

This task is marked as **Phase 3: Advanced Research** in ARCHITECTURE_v4.0.md and is completely optional. The Day 1 system is production-ready without this enhancement.

## Overview

Fine-tune a language model on financial domain knowledge to create a QuantConnect specialist model using Parameter-Efficient Fine-Tuning (LoRA).

## Approach

### 1. Training Data Curation (2 days)

**WorldQuant Alphas Dataset:**
- Extract all 101 alphas from WorldQuant paper
- Format as instruction-completion pairs
- Example:
  ```json
  {
    "instruction": "Create a volume-price correlation momentum strategy",
    "completion": "Alpha #1: (-1 * correlation(rank(delta(log(volume), 1)), rank(((close - open) / open)), 6))"
  }
  ```

**QuantConnect Examples:**
- Scrape official QC documentation examples
- Extract strategy code from QC forums
- Include backtested strategies from QC community
- Format: natural language description → working QC code

**Trading Patterns:**
- Mean reversion strategies
- Momentum strategies
- Arbitrage patterns
- Risk management formulas

Total dataset size: ~1000-2000 high-quality examples

### 2. Model Selection (1 day)

**Base Model Options:**
1. **CodeLlama-7B** (Recommended)
   - Optimized for code generation
   - 7B parameters = efficient fine-tuning
   - Strong Python understanding
   
2. **Mistral-7B**
   - General purpose but excellent
   - Good at following instructions
   
3. **Gemma-2B** (Budget option)
   - Smallest, fastest training
   - Good for proof of concept

### 3. LoRA Configuration (1 day)

**Parameters:**
```python
from peft import LoraConfig, get_peft_model

lora_config = LoraConfig(
    r=16,                    # Rank of update matrices
    lora_alpha=32,          # Scaling factor
    target_modules=[         # Which layers to adapt
        "q_proj",
        "v_proj",
        "k_proj",
        "o_proj"
    ],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)
```

**Why LoRA?**
- Only trains 0.1-1% of model parameters
- 10-100x cheaper than full fine-tuning
- Adapter can be swapped at runtime
- Preserves base model capabilities

### 4. Training Pipeline (2-3 days)

**Setup:**
```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import get_peft_model
import torch

# Load base model
model = AutoModelForCausalLM.from_pretrained(
    "codellama/CodeLlama-7b-hf",
    torch_dtype=torch.float16,
    device_map="auto"
)

# Apply LoRA
model = get_peft_model(model, lora_config)

# Training arguments
training_args = TrainingArguments(
    output_dir="./quantconnect-lora",
    num_train_epochs=3,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=8,
    learning_rate=2e-4,
    fp16=True,
    logging_steps=10,
    save_strategy="epoch"
)
```

**Training Data Format:**
```python
def format_training_example(instruction, completion):
    return f"""<s>[INST] {instruction} [/INST]
{completion}</s>"""
```

**Cost Estimate:**
- GPU: 1x A100 (40GB) or 2x RTX 4090
- Time: 8-12 hours training
- Cost: $50-100 (on Lambda Labs / RunPod)

### 5. Evaluation (1 day)

**Metrics:**
- Code compilation rate
- Backtest success rate
- Sharpe ratio of generated strategies
- Human evaluation (strategy quality)

**Benchmark:**
Compare vs base models on:
1. Generate 20 strategies from prompts
2. Measure: syntax errors, runtime errors, backtest success
3. Target: >80% compilable, >50% backtest-ready

### 6. Integration (1 day)

**Update autonomous_build.py:**
```python
from peft import PeftModel

# Load LoRA adapter
base_model = AutoModelForCausalLM.from_pretrained("codellama/CodeLlama-7b-hf")
model = PeftModel.from_pretrained(base_model, "./quantconnect-lora")

# Use in generation
def generate_strategy(prompt):
    inputs = tokenizer(prompt, return_tensors="pt")
    outputs = model.generate(**inputs, max_length=2048)
    return tokenizer.decode(outputs[0])
```

**Fallback Chain:**
1. Finance-tuned LLM (if available)
2. Claude/GPT-4 (production default)
3. Gemini Flash (cost optimization)

## Expected Impact

**Improvements:**
- 10-20% better strategy quality (per architecture)
- Faster code generation (domain-specific)
- More WorldQuant-like alphas
- Reduced API costs (local inference)

**Marginal Value:**
- Existing models (Claude, GPT-4) are already excellent
- RAG provides similar domain knowledge
- Cost savings minimal (~$0.10/build)
- **Conclusion:** Nice-to-have, not critical

## Why Deferred?

Per ARCHITECTURE_v4.0.md Section 11:
> "Conclusion: Day 1 system is 80-90% as capable as fully-enhanced system. Later phases are optimizations, not requirements."

**Trade-offs:**
- ✅ Day 1 system works without this
- ✅ Can deploy now and fine-tune later
- ✅ More urgent: get live strategies trading
- ⏸️ Fine-tuning is research, not ops

## Future Implementation

When ready to implement:

1. **Collect live data first**
   - Let autonomous builder run for 1-2 months
   - Collect successful strategies
   - Use real-world data for training

2. **Iterative approach**
   - Start with small LoRA (r=8)
   - Test on validation set
   - Scale up if beneficial

3. **A/B testing**
   - Deploy alongside Claude/GPT-4
   - Measure quality differences
   - Only switch if clear improvement

## References

- LoRA Paper: https://arxiv.org/abs/2106.09685
- PEFT Library: https://github.com/huggingface/peft
- CodeLlama: https://github.com/facebookresearch/codellama
- LLM Quant Framework: https://arxiv.org/abs/2409.06289

## Estimated Timeline

If prioritized in the future:
- Data curation: 2 days
- Model selection: 1 day
- LoRA setup: 1 day
- Training: 2-3 days
- Evaluation: 1 day
- Integration: 1 day

**Total: 1 week** (consistent with architecture estimate)

## Conclusion

Task 13 is **deferred but documented** for future implementation. The current system with Claude/GPT-4 + RAG is sufficient for production deployment.

Focus remains on:
✅ Getting strategies live
✅ Measuring real-world performance
✅ Collecting production data for future training
