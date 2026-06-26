"""Model evaluation utilities: metrics, plots, and reporting."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    auc,
    calibration_curve,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)


def classification_report_dict(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_prob: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Return a dict of standard classification metrics.

    Parameters
    ----------
    y_true:
        Ground-truth labels.
    y_pred:
        Predicted labels.
    y_prob:
        Predicted probabilities for the positive class (used for AUC).

    Returns
    -------
    Dict with accuracy, precision, recall, f1, and optionally roc_auc.
    """
    report: Dict[str, Any] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, average="weighted", zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, average="weighted", zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
    }
    if y_prob is not None:
        try:
            # Binary case
            if y_prob.ndim == 1 or y_prob.shape[1] == 1:
                prob = y_prob.ravel()
                report["roc_auc"] = float(roc_auc_score(y_true, prob))
            else:
                report["roc_auc"] = float(
                    roc_auc_score(y_true, y_prob, multi_class="ovr", average="weighted")
                )
        except ValueError:
            # e.g. only one class present in y_true
            report["roc_auc"] = None
    return report


def plot_roc_auc(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    title: str = "ROC Curve",
    save_path: Optional[Union[str, Path]] = None,
) -> None:
    """Plot the ROC curve and display / save it.

    Parameters
    ----------
    y_true:
        Ground-truth binary labels.
    y_prob:
        Predicted probabilities for the positive class.
    title:
        Plot title.
    save_path:
        If provided, save the figure to this path instead of showing it.
    """
    import matplotlib.pyplot as plt

    fpr, tpr, _ = roc_curve(y_true, y_prob)
    roc_auc_val = auc(fpr, tpr)

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.plot(fpr, tpr, lw=2, label=f"AUC = {roc_auc_val:.3f}")
    ax.plot([0, 1], [0, 1], linestyle="--", color="grey", lw=1)
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title(title)
    ax.legend(loc="lower right")
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1.05])
    fig.tight_layout()

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(save_path), dpi=150)
        plt.close(fig)
    else:
        plt.show()


def plot_confusion_matrix(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    labels: Optional[List[str]] = None,
    save_path: Optional[Union[str, Path]] = None,
) -> None:
    """Plot a confusion matrix heatmap.

    Parameters
    ----------
    y_true:
        Ground-truth labels.
    y_pred:
        Predicted labels.
    labels:
        Display labels for each class.
    save_path:
        If provided, save the figure to this path.
    """
    import matplotlib.pyplot as plt

    cm = confusion_matrix(y_true, y_pred)
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
    ax.set_title("Confusion Matrix")
    fig.colorbar(im, ax=ax)

    n_classes = cm.shape[0]
    tick_marks = np.arange(n_classes)
    if labels:
        ax.set_xticks(tick_marks)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_yticks(tick_marks)
        ax.set_yticklabels(labels)
    else:
        ax.set_xticks(tick_marks)
        ax.set_yticks(tick_marks)

    # Annotate cells
    thresh = cm.max() / 2.0
    for i in range(n_classes):
        for j in range(n_classes):
            ax.text(
                j, i, format(cm[i, j], "d"),
                ha="center", va="center",
                color="white" if cm[i, j] > thresh else "black",
            )

    ax.set_ylabel("True label")
    ax.set_xlabel("Predicted label")
    fig.tight_layout()

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(save_path), dpi=150)
        plt.close(fig)
    else:
        plt.show()


def plot_calibration_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
    save_path: Optional[Union[str, Path]] = None,
) -> None:
    """Plot a calibration (reliability) curve.

    Parameters
    ----------
    y_true:
        Ground-truth binary labels.
    y_prob:
        Predicted probabilities for the positive class.
    n_bins:
        Number of calibration bins.
    save_path:
        If provided, save the figure to this path.
    """
    import matplotlib.pyplot as plt

    prob_true, prob_pred = calibration_curve(y_true, y_prob, n_bins=n_bins)

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.plot(prob_pred, prob_true, marker="o", lw=2, label="Model")
    ax.plot([0, 1], [0, 1], linestyle="--", color="grey", lw=1, label="Perfectly calibrated")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Fraction of positives")
    ax.set_title("Calibration Curve")
    ax.legend(loc="lower right")
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1.05])
    fig.tight_layout()

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(save_path), dpi=150)
        plt.close(fig)
    else:
        plt.show()


def regression_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> Dict[str, float]:
    """Compute standard regression metrics.

    Returns
    -------
    Dict with ``mae``, ``rmse``, and ``r2``.
    """
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
    }
