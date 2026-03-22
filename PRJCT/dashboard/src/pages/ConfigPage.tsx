import ConfigEditor from "../components/config/ConfigEditor";

export default function ConfigPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold">Configuration</h1>
      <ConfigEditor />
    </div>
  );
}
