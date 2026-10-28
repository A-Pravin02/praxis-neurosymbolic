# nanoGPT configuration for the from-scratch World Cup model.
#
# SIZING, stated plainly because it governs how the result may be read.
#
# The corpus is 292,948 training tokens. Measured parameter counts at vocab 4096:
#
#   L4 H4 E256 ctx256  ->   4.20M params  (0.070 tokens/parameter)
#   L6 H6 E384 ctx256  ->  12.19M params  (0.024 tokens/parameter)
#   L8 H8 E512 ctx512  ->  27.27M params  (0.011 tokens/parameter)
#
# A compute-optimal budget is roughly 20 tokens per parameter. Every configuration
# above is two orders of magnitude past that, so the model WILL memorise the corpus.
# No choice here avoids it; 293k tokens cannot support a model of useful size.
#
# That is acceptable for this experiment, and it is worth being clear why. The model is
# not the contribution. Its role is to be a weak generator that emits checkable football
# claims: true ones for matches it memorised, confabulated ones for the 137 matches
# withheld from training. The research question is whether the symbolic layer catches
# the second kind. A stronger model would make the test easier, not more informative.
#
# The smallest configuration is therefore the default: it is the least
# over-parameterised, trains in minutes, and keeps the claim about the model modest.
#
# Measured on the target GPU (RTX 4050 Laptop, 6.44 GB) at vocab 8192:
#   L8 H8 E512 -> 50,903 tok/s, 2.48 GB.  This configuration is far smaller again.
#
# Run:
#   python nanogpt/train.py nanogpt/config/train_football.py
#   python nanogpt/train.py nanogpt/config/train_football.py --max_iters=200  # pilot

out_dir = 'artifacts/football/nanogpt'
eval_interval = 100
eval_iters = 40
log_interval = 20

# The corpus is small and overfits fast, so only improvements are kept.
always_save_checkpoint = False
wandb_log = False

dataset = 'football'          # nanogpt/data/football/{train.bin,val.bin,meta.pkl}

# 32 x 2 x 256 = 16,384 tokens per optimizer step, ~18 steps per epoch.
batch_size = 32
gradient_accumulation_steps = 2
block_size = 256

# ~4.2M parameters.
n_layer = 4
n_head = 4
n_embd = 256
dropout = 0.2                 # heavy, deliberately: the corpus is tiny
bias = False

learning_rate = 1e-3          # small model, small batches tolerate a higher rate
max_iters = 3000
lr_decay_iters = 3000
min_lr = 1e-4
beta2 = 0.99                  # fewer tokens per step means noisier gradients
warmup_iters = 100
weight_decay = 0.1
grad_clip = 1.0

# torch.compile is unreliable on Windows and the speedup does not justify the failure
# mode. The throughput figures above were measured with it off.
compile = False

device = 'cuda'
dtype = 'bfloat16'            # falls back to float16 + GradScaler if unsupported
