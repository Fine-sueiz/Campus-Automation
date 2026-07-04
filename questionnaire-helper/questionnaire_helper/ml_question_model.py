import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def char_ngrams(text: str, max_n: int = 3) -> list[str]:
    text = normalize(text)
    grams: list[str] = []
    for n in range(1, max_n + 1):
        grams.extend(text[i : i + n] for i in range(max(0, len(text) - n + 1)))
    return grams


def hashed_features(text: str, dim: int) -> list[float]:
    values = [0.0] * dim
    for gram in char_ngrams(text):
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=4).digest()
        values[int.from_bytes(digest, "little") % dim] += 1.0
    length = math.sqrt(sum(item * item for item in values)) or 1.0
    return [item / length for item in values]


def softmax(values: list[float]) -> list[float]:
    m = max(values)
    exps = [math.exp(item - m) for item in values]
    total = sum(exps) or 1.0
    return [item / total for item in exps]


class QuestionClassifier:
    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        labels: list[str],
        w1: list[list[float]],
        b1: list[float],
        w2: list[list[float]],
        b2: list[float],
    ) -> None:
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.labels = labels
        self.w1 = w1
        self.b1 = b1
        self.w2 = w2
        self.b2 = b2

    @classmethod
    def new(cls, input_dim: int, hidden_dim: int, labels: list[str], seed: int = 42) -> "QuestionClassifier":
        rng = random.Random(seed)
        w1 = [[rng.uniform(-0.08, 0.08) for _ in range(input_dim)] for _ in range(hidden_dim)]
        b1 = [0.0 for _ in range(hidden_dim)]
        w2 = [[rng.uniform(-0.08, 0.08) for _ in range(hidden_dim)] for _ in labels]
        b2 = [0.0 for _ in labels]
        return cls(input_dim, hidden_dim, labels, w1, b1, w2, b2)

    @classmethod
    def load(cls, path: Path) -> "QuestionClassifier":
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            int(data["input_dim"]),
            int(data["hidden_dim"]),
            list(data["labels"]),
            data["w1"],
            data["b1"],
            data["w2"],
            data["b2"],
        )

    def save(self, path: Path) -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "input_dim": self.input_dim,
                    "hidden_dim": self.hidden_dim,
                    "labels": self.labels,
                    "w1": self.w1,
                    "b1": self.b1,
                    "w2": self.w2,
                    "b2": self.b2,
                },
                f,
                ensure_ascii=False,
            )

    def forward(self, text: str) -> tuple[list[float], list[float], list[float]]:
        x = hashed_features(text, self.input_dim)
        hidden = []
        for row, bias in zip(self.w1, self.b1):
            z = sum(weight * value for weight, value in zip(row, x)) + bias
            hidden.append(math.tanh(z))
        logits = []
        for row, bias in zip(self.w2, self.b2):
            logits.append(sum(weight * value for weight, value in zip(row, hidden)) + bias)
        return x, hidden, softmax(logits)

    def predict(self, text: str) -> tuple[str, float]:
        _, _, probs = self.forward(text)
        index = max(range(len(probs)), key=lambda i: probs[i])
        return self.labels[index], probs[index]

    def train(self, samples: list[dict[str, str]], epochs: int, lr: float) -> None:
        label_to_index = {label: i for i, label in enumerate(self.labels)}
        rng = random.Random(42)
        for epoch in range(1, epochs + 1):
            rng.shuffle(samples)
            total_loss = 0.0
            correct = 0
            for sample in samples:
                text = sample["text"]
                target = label_to_index[sample["label"]]
                x, hidden, probs = self.forward(text)
                total_loss -= math.log(max(probs[target], 1e-12))
                if max(range(len(probs)), key=lambda i: probs[i]) == target:
                    correct += 1

                d_logits = probs[:]
                d_logits[target] -= 1.0

                d_hidden = [0.0] * self.hidden_dim
                for out_i, grad in enumerate(d_logits):
                    for h_i in range(self.hidden_dim):
                        d_hidden[h_i] += grad * self.w2[out_i][h_i]
                        self.w2[out_i][h_i] -= lr * grad * hidden[h_i]
                    self.b2[out_i] -= lr * grad

                for h_i, grad in enumerate(d_hidden):
                    dz = grad * (1.0 - hidden[h_i] * hidden[h_i])
                    for x_i, value in enumerate(x):
                        if value:
                            self.w1[h_i][x_i] -= lr * dz * value
                    self.b1[h_i] -= lr * dz

            if epoch == 1 or epoch % 20 == 0 or epoch == epochs:
                loss = total_loss / max(len(samples), 1)
                acc = correct / max(len(samples), 1)
                print(f"epoch={epoch} loss={loss:.4f} acc={acc:.2%}")


def load_samples(path: Path) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            text = str(item.get("text", "")).strip()
            label = str(item.get("label", "")).strip()
            if not text or not label:
                raise ValueError(f"{path}:{line_no} requires non-empty text and label")
            samples.append({"text": text, "label": label})
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a small local neural text classifier.")
    parser.add_argument("--train", default="training_data.jsonl", help="JSONL training data path.")
    parser.add_argument("--out", default="question_model.json", help="Output model path.")
    parser.add_argument("--input-dim", type=int, default=256)
    parser.add_argument("--hidden-dim", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=160)
    parser.add_argument("--lr", type=float, default=0.08)
    args = parser.parse_args()

    samples = load_samples(Path(args.train))
    labels = sorted({sample["label"] for sample in samples})
    model = QuestionClassifier.new(args.input_dim, args.hidden_dim, labels)
    model.train(samples, args.epochs, args.lr)
    out_path = Path(args.out)
    if out_path.exists() and out_path.is_dir():
        out_path = out_path / "question_model.json"
    elif str(args.out).strip() in {".", "./", ".\\"}:
        out_path = Path("question_model.json")

    try:
        model.save(out_path)
    except PermissionError as exc:
        fallback = out_path.with_name(f"{out_path.stem}_new{out_path.suffix or '.json'}")
        model.save(fallback)
        print(f"could not overwrite {out_path}: {exc}")
        print(f"saved model to fallback file {fallback}")
        print("update config.json ml_classifier.model_path to this fallback file if needed")
        return

    print(f"saved model to {out_path}; labels={', '.join(labels)}")


if __name__ == "__main__":
    main()
