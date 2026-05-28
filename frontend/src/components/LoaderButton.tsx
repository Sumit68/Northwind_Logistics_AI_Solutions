import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  loading?: boolean;
  disabled?: boolean;
  variant?: "primary" | "secondary";
  type?: "button" | "submit";
  onClick?: () => void;
};

export default function LoaderButton({
  children,
  loading = false,
  disabled = false,
  variant = "primary",
  type = "button",
  onClick,
}: Props) {
  return (
    <button
      type={type}
      className={`btn ${variant === "secondary" ? "btn-secondary" : "btn-primary"}`}
      disabled={disabled || loading}
      onClick={onClick}
    >
      {loading && <span className="btn-spinner" aria-hidden />}
      <span>{loading ? "Please wait…" : children}</span>
    </button>
  );
}
