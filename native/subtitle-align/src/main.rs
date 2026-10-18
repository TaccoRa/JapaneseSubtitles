//! JSON intervals in, JSON offsets out. Never opens or writes an SRT.
use alass_core::{align, standard_scoring, NoProgressHandler, TimePoint, TimeSpan};
use serde::Deserialize;
use std::io::{self, Read};

#[derive(Deserialize)]
struct Request { source: Vec<[f64; 2]>, reference: Vec<[f64; 2]> }

fn spans(values: &[[f64; 2]]) -> Result<Vec<TimeSpan>, &'static str> {
    if values.len() > 10000 { return Err("too many intervals"); }
    values.iter().map(|[a,b]| {
        if !a.is_finite() || !b.is_finite() || a >= b || a.abs() > 1e7 || b.abs() > 1e7 {
            return Err("invalid interval");
        }
        Ok(TimeSpan::new(TimePoint::from((a * 1000.0).round() as i64),
                         TimePoint::from((b * 1000.0).round() as i64)))
    }).collect()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin().take(4_000_000).read_to_string(&mut input)?;
    let request: Request = serde_json::from_str(&input)?;
    let source = spans(&request.source)?;
    let reference = spans(&request.reference)?;
    let (deltas, score) = align(&reference, &source, 7.0, Some(2.0), standard_scoring, NoProgressHandler);
    let offsets: Vec<f64> = deltas.iter().map(|d| d.as_i64() as f64 / 1000.0).collect();
    println!("{}", serde_json::json!({"offsets": offsets, "score": score}));
    Ok(())
}
