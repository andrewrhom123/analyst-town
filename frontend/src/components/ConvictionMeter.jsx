export default function ConvictionMeter({ value }) {
  const v = value ?? 0;
  return (
    <div className="meter" role="img" aria-label={value == null ? "No conviction yet" : `Conviction ${value} of 10`}>
      {Array.from({ length: 10 }, (_, i) => <i key={i} className={i < v ? "on" : ""} />)}
      <span className="val">{value == null ? "–" : `${value}/10`}</span>
    </div>
  );
}
