interface Props {
  label: string;
  variant: "green" | "red" | "yellow" | "gray";
}

const colors = {
  green: "bg-green-500/20 text-green-400 border-green-500/30",
  red: "bg-red-500/20 text-red-400 border-red-500/30",
  yellow: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  gray: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

export default function StatusBadge({ label, variant }: Props) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${colors[variant]}`}>
      {label}
    </span>
  );
}
