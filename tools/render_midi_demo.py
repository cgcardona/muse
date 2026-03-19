#!/usr/bin/env python3
"""MIDI Demo Page generator — Bach Prelude BWV 846 × Muse VCS.

Outputs: artifacts/midi-demo.html

Demonstrates Muse's 21-dimensional MIDI version control using
Bach's Prelude No. 1 in C Major (BWV 846).

Note data sourced from the music21 corpus (MuseScore 1.3 transcription,
2013-07-09, musescore.com/score/117279). Bach died 1750 — public domain.
Format: [pitch_midi, velocity, start_sec, duration_sec, measure, voice]
"""

import json
import logging
import pathlib

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Bach BWV 846 — note data extracted from music21 corpus MusicXML
# Voices: 1=treble arpeggios (pitch 55-81), 5=bass long notes (36-60),
#         6=inner voice (47-64). Tempo: 66 BPM, Duration: ~127s.
# ──────────────────────────────────────────────────────────────────────────────
_BACH_NOTES_JSON = (
    "[[60,64,0.0,1.5833,1,5],[64,64,0.2083,0.5938,1,6],[67,80,0.4167,0.1979,1,1],"
    "[72,80,0.625,0.1979,1,1],[76,80,0.8333,0.1979,1,1],[67,80,1.0417,0.1979,1,1],"
    "[72,80,1.25,0.1979,1,1],[76,80,1.4583,0.1979,1,1],[60,64,1.6667,1.5833,1,5],"
    "[64,64,1.875,0.5938,1,6],[67,80,2.0833,0.1979,1,1],[72,80,2.2917,0.1979,1,1],"
    "[76,80,2.5,0.1979,1,1],[67,80,2.7083,0.1979,1,1],[72,80,2.9167,0.1979,1,1],"
    "[76,80,3.125,0.1979,1,1],[60,64,3.3333,1.5833,2,5],[62,64,3.5417,0.5938,2,6],"
    "[69,80,3.75,0.1979,2,1],[74,80,3.9583,0.1979,2,1],[77,80,4.1667,0.1979,2,1],"
    "[69,80,4.375,0.1979,2,1],[74,80,4.5833,0.1979,2,1],[77,80,4.7917,0.1979,2,1],"
    "[60,64,5.0,1.5833,2,5],[62,64,5.2083,0.5938,2,6],[69,80,5.4167,0.1979,2,1],"
    "[74,80,5.625,0.1979,2,1],[77,80,5.8333,0.1979,2,1],[69,80,6.0417,0.1979,2,1],"
    "[74,80,6.25,0.1979,2,1],[77,80,6.4583,0.1979,2,1],[59,64,6.6667,1.5833,3,5],"
    "[62,64,6.875,0.5938,3,6],[67,80,7.0833,0.1979,3,1],[74,80,7.2917,0.1979,3,1],"
    "[77,80,7.5,0.1979,3,1],[67,80,7.7083,0.1979,3,1],[74,80,7.9167,0.1979,3,1],"
    "[77,80,8.125,0.1979,3,1],[59,64,8.3333,1.5833,3,5],[62,64,8.5417,0.5938,3,6],"
    "[67,80,8.75,0.1979,3,1],[74,80,8.9583,0.1979,3,1],[77,80,9.1667,0.1979,3,1],"
    "[67,80,9.375,0.1979,3,1],[74,80,9.5833,0.1979,3,1],[77,80,9.7917,0.1979,3,1],"
    "[60,64,10.0,1.5833,4,5],[64,64,10.2083,0.5938,4,6],[67,80,10.4167,0.1979,4,1],"
    "[72,80,10.625,0.1979,4,1],[76,80,10.8333,0.1979,4,1],[67,80,11.0417,0.1979,4,1],"
    "[72,80,11.25,0.1979,4,1],[76,80,11.4583,0.1979,4,1],[60,64,11.6667,1.5833,4,5],"
    "[64,64,11.875,0.5938,4,6],[67,80,12.0833,0.1979,4,1],[72,80,12.2917,0.1979,4,1],"
    "[76,80,12.5,0.1979,4,1],[67,80,12.7083,0.1979,4,1],[72,80,12.9167,0.1979,4,1],"
    "[76,80,13.125,0.1979,4,1],[60,64,13.3333,1.5833,5,5],[64,64,13.5417,0.5938,5,6],"
    "[69,80,13.75,0.1979,5,1],[76,80,13.9583,0.1979,5,1],[81,80,14.1667,0.1979,5,1],"
    "[69,80,14.375,0.1979,5,1],[76,80,14.5833,0.1979,5,1],[81,80,14.7917,0.1979,5,1],"
    "[60,64,15.0,1.5833,5,5],[64,64,15.2083,0.5938,5,6],[69,80,15.4167,0.1979,5,1],"
    "[76,80,15.625,0.1979,5,1],[81,80,15.8333,0.1979,5,1],[69,80,16.0417,0.1979,5,1],"
    "[76,80,16.25,0.1979,5,1],[81,80,16.4583,0.1979,5,1],[60,64,16.6667,1.5833,6,5],"
    "[62,64,16.875,0.5938,6,6],[66,80,17.0833,0.1979,6,1],[69,80,17.2917,0.1979,6,1],"
    "[74,80,17.5,0.1979,6,1],[66,80,17.7083,0.1979,6,1],[69,80,17.9167,0.1979,6,1],"
    "[74,80,18.125,0.1979,6,1],[60,64,18.3333,1.5833,6,5],[62,64,18.5417,0.5938,6,6],"
    "[66,80,18.75,0.1979,6,1],[69,80,18.9583,0.1979,6,1],[74,80,19.1667,0.1979,6,1],"
    "[66,80,19.375,0.1979,6,1],[69,80,19.5833,0.1979,6,1],[74,80,19.7917,0.1979,6,1],"
    "[59,64,20.0,1.5833,7,5],[62,64,20.2083,0.5938,7,6],[67,80,20.4167,0.1979,7,1],"
    "[74,80,20.625,0.1979,7,1],[79,80,20.8333,0.1979,7,1],[67,80,21.0417,0.1979,7,1],"
    "[74,80,21.25,0.1979,7,1],[79,80,21.4583,0.1979,7,1],[59,64,21.6667,1.5833,7,5],"
    "[62,64,21.875,0.5938,7,6],[67,80,22.0833,0.1979,7,1],[74,80,22.2917,0.1979,7,1],"
    "[79,80,22.5,0.1979,7,1],[67,80,22.7083,0.1979,7,1],[74,80,22.9167,0.1979,7,1],"
    "[79,80,23.125,0.1979,7,1],[59,64,23.3333,1.5833,8,5],[60,64,23.5417,0.5938,8,6],"
    "[64,80,23.75,0.1979,8,1],[67,80,23.9583,0.1979,8,1],[72,80,24.1667,0.1979,8,1],"
    "[64,80,24.375,0.1979,8,1],[67,80,24.5833,0.1979,8,1],[72,80,24.7917,0.1979,8,1],"
    "[59,64,25.0,1.5833,8,5],[60,64,25.2083,0.5938,8,6],[64,80,25.4167,0.1979,8,1],"
    "[67,80,25.625,0.1979,8,1],[72,80,25.8333,0.1979,8,1],[64,80,26.0417,0.1979,8,1],"
    "[67,80,26.25,0.1979,8,1],[72,80,26.4583,0.1979,8,1],[57,64,26.6667,1.5833,9,5],"
    "[60,64,26.875,0.5938,9,6],[64,80,27.0833,0.1979,9,1],[67,80,27.2917,0.1979,9,1],"
    "[72,80,27.5,0.1979,9,1],[64,80,27.7083,0.1979,9,1],[67,80,27.9167,0.1979,9,1],"
    "[72,80,28.125,0.1979,9,1],[57,64,28.3333,1.5833,9,5],[60,64,28.5417,0.5938,9,6],"
    "[64,80,28.75,0.1979,9,1],[67,80,28.9583,0.1979,9,1],[72,80,29.1667,0.1979,9,1],"
    "[64,80,29.375,0.1979,9,1],[67,80,29.5833,0.1979,9,1],[72,80,29.7917,0.1979,9,1],"
    "[50,64,30.0,1.5833,10,5],[57,64,30.2083,0.5938,10,6],[62,80,30.4167,0.1979,10,1],"
    "[66,80,30.625,0.1979,10,1],[72,80,30.8333,0.1979,10,1],[62,80,31.0417,0.1979,10,1],"
    "[66,80,31.25,0.1979,10,1],[72,80,31.4583,0.1979,10,1],[50,64,31.6667,1.5833,10,5],"
    "[57,64,31.875,0.5938,10,6],[62,80,32.0833,0.1979,10,1],[66,80,32.2917,0.1979,10,1],"
    "[72,80,32.5,0.1979,10,1],[62,80,32.7083,0.1979,10,1],[66,80,32.9167,0.1979,10,1],"
    "[72,80,33.125,0.1979,10,1],[55,64,33.3333,1.5833,11,5],[59,64,33.5417,0.5938,11,6],"
    "[62,80,33.75,0.1979,11,1],[67,80,33.9583,0.1979,11,1],[71,80,34.1667,0.1979,11,1],"
    "[62,80,34.375,0.1979,11,1],[67,80,34.5833,0.1979,11,1],[71,80,34.7917,0.1979,11,1],"
    "[55,64,35.0,1.5833,11,5],[59,64,35.2083,0.5938,11,6],[62,80,35.4167,0.1979,11,1],"
    "[67,80,35.625,0.1979,11,1],[71,80,35.8333,0.1979,11,1],[62,80,36.0417,0.1979,11,1],"
    "[67,80,36.25,0.1979,11,1],[71,80,36.4583,0.1979,11,1],[55,64,36.6667,1.5833,12,5],"
    "[58,64,36.875,0.5938,12,6],[64,80,37.0833,0.1979,12,1],[67,80,37.2917,0.1979,12,1],"
    "[73,80,37.5,0.1979,12,1],[64,80,37.7083,0.1979,12,1],[67,80,37.9167,0.1979,12,1],"
    "[73,80,38.125,0.1979,12,1],[55,64,38.3333,1.5833,12,5],[58,64,38.5417,0.5938,12,6],"
    "[64,80,38.75,0.1979,12,1],[67,80,38.9583,0.1979,12,1],[73,80,39.1667,0.1979,12,1],"
    "[64,80,39.375,0.1979,12,1],[67,80,39.5833,0.1979,12,1],[73,80,39.7917,0.1979,12,1],"
    "[53,64,40.0,1.5833,13,5],[57,64,40.2083,0.5938,13,6],[62,80,40.4167,0.1979,13,1],"
    "[69,80,40.625,0.1979,13,1],[74,80,40.8333,0.1979,13,1],[62,80,41.0417,0.1979,13,1],"
    "[69,80,41.25,0.1979,13,1],[74,80,41.4583,0.1979,13,1],[53,64,41.6667,1.5833,13,5],"
    "[57,64,41.875,0.5938,13,6],[62,80,42.0833,0.1979,13,1],[69,80,42.2917,0.1979,13,1],"
    "[74,80,42.5,0.1979,13,1],[62,80,42.7083,0.1979,13,1],[69,80,42.9167,0.1979,13,1],"
    "[74,80,43.125,0.1979,13,1],[53,64,43.3333,1.5833,14,5],[56,64,43.5417,0.5938,14,6],"
    "[62,80,43.75,0.1979,14,1],[65,80,43.9583,0.1979,14,1],[71,80,44.1667,0.1979,14,1],"
    "[62,80,44.375,0.1979,14,1],[65,80,44.5833,0.1979,14,1],[71,80,44.7917,0.1979,14,1],"
    "[53,64,45.0,1.5833,14,5],[56,64,45.2083,0.5938,14,6],[62,80,45.4167,0.1979,14,1],"
    "[65,80,45.625,0.1979,14,1],[71,80,45.8333,0.1979,14,1],[62,80,46.0417,0.1979,14,1],"
    "[65,80,46.25,0.1979,14,1],[71,80,46.4583,0.1979,14,1],[52,64,46.6667,1.5833,15,5],"
    "[55,64,46.875,0.5938,15,6],[60,80,47.0833,0.1979,15,1],[67,80,47.2917,0.1979,15,1],"
    "[72,80,47.5,0.1979,15,1],[60,80,47.7083,0.1979,15,1],[67,80,47.9167,0.1979,15,1],"
    "[72,80,48.125,0.1979,15,1],[52,64,48.3333,1.5833,15,5],[55,64,48.5417,0.5938,15,6],"
    "[60,80,48.75,0.1979,15,1],[67,80,48.9583,0.1979,15,1],[72,80,49.1667,0.1979,15,1],"
    "[60,80,49.375,0.1979,15,1],[67,80,49.5833,0.1979,15,1],[72,80,49.7917,0.1979,15,1],"
    "[52,64,50.0,1.5833,16,5],[53,64,50.2083,0.5938,16,6],[57,80,50.4167,0.1979,16,1],"
    "[60,80,50.625,0.1979,16,1],[65,80,50.8333,0.1979,16,1],[57,80,51.0417,0.1979,16,1],"
    "[60,80,51.25,0.1979,16,1],[65,80,51.4583,0.1979,16,1],[52,64,51.6667,1.5833,16,5],"
    "[53,64,51.875,0.5938,16,6],[57,80,52.0833,0.1979,16,1],[60,80,52.2917,0.1979,16,1],"
    "[65,80,52.5,0.1979,16,1],[57,80,52.7083,0.1979,16,1],[60,80,52.9167,0.1979,16,1],"
    "[65,80,53.125,0.1979,16,1],[50,64,53.3333,1.5833,17,5],[53,64,53.5417,0.5938,17,6],"
    "[57,80,53.75,0.1979,17,1],[60,80,53.9583,0.1979,17,1],[65,80,54.1667,0.1979,17,1],"
    "[57,80,54.375,0.1979,17,1],[60,80,54.5833,0.1979,17,1],[65,80,54.7917,0.1979,17,1],"
    "[50,64,55.0,1.5833,17,5],[53,64,55.2083,0.5938,17,6],[57,80,55.4167,0.1979,17,1],"
    "[60,80,55.625,0.1979,17,1],[65,80,55.8333,0.1979,17,1],[57,80,56.0417,0.1979,17,1],"
    "[60,80,56.25,0.1979,17,1],[65,80,56.4583,0.1979,17,1],[43,64,56.6667,1.5833,18,5],"
    "[50,64,56.875,0.5938,18,6],[55,80,57.0833,0.1979,18,1],[59,80,57.2917,0.1979,18,1],"
    "[65,80,57.5,0.1979,18,1],[55,80,57.7083,0.1979,18,1],[59,80,57.9167,0.1979,18,1],"
    "[65,80,58.125,0.1979,18,1],[43,64,58.3333,1.5833,18,5],[50,64,58.5417,0.5938,18,6],"
    "[55,80,58.75,0.1979,18,1],[59,80,58.9583,0.1979,18,1],[65,80,59.1667,0.1979,18,1],"
    "[55,80,59.375,0.1979,18,1],[59,80,59.5833,0.1979,18,1],[65,80,59.7917,0.1979,18,1],"
    "[48,64,60.0,1.5833,19,5],[52,64,60.2083,0.5938,19,6],[55,80,60.4167,0.1979,19,1],"
    "[60,80,60.625,0.1979,19,1],[64,80,60.8333,0.1979,19,1],[55,80,61.0417,0.1979,19,1],"
    "[60,80,61.25,0.1979,19,1],[64,80,61.4583,0.1979,19,1],[48,64,61.6667,1.5833,19,5],"
    "[52,64,61.875,0.5938,19,6],[55,80,62.0833,0.1979,19,1],[60,80,62.2917,0.1979,19,1],"
    "[64,80,62.5,0.1979,19,1],[55,80,62.7083,0.1979,19,1],[60,80,62.9167,0.1979,19,1],"
    "[64,80,63.125,0.1979,19,1],[48,64,63.3333,1.5833,20,5],[55,64,63.5417,0.5938,20,6],"
    "[58,80,63.75,0.1979,20,1],[60,80,63.9583,0.1979,20,1],[64,80,64.1667,0.1979,20,1],"
    "[58,80,64.375,0.1979,20,1],[60,80,64.5833,0.1979,20,1],[64,80,64.7917,0.1979,20,1],"
    "[48,64,65.0,1.5833,20,5],[55,64,65.2083,0.5938,20,6],[58,80,65.4167,0.1979,20,1],"
    "[60,80,65.625,0.1979,20,1],[64,80,65.8333,0.1979,20,1],[58,80,66.0417,0.1979,20,1],"
    "[60,80,66.25,0.1979,20,1],[64,80,66.4583,0.1979,20,1],[41,64,66.6667,1.5833,21,5],"
    "[53,64,66.875,0.5938,21,6],[57,80,67.0833,0.1979,21,1],[60,80,67.2917,0.1979,21,1],"
    "[64,80,67.5,0.1979,21,1],[57,80,67.7083,0.1979,21,1],[60,80,67.9167,0.1979,21,1],"
    "[64,80,68.125,0.1979,21,1],[41,64,68.3333,1.5833,21,5],[53,64,68.5417,0.5938,21,6],"
    "[57,80,68.75,0.1979,21,1],[60,80,68.9583,0.1979,21,1],[64,80,69.1667,0.1979,21,1],"
    "[57,80,69.375,0.1979,21,1],[60,80,69.5833,0.1979,21,1],[64,80,69.7917,0.1979,21,1],"
    "[42,64,70.0,1.5833,22,5],[48,64,70.2083,0.5938,22,6],[57,80,70.4167,0.1979,22,1],"
    "[60,80,70.625,0.1979,22,1],[63,80,70.8333,0.1979,22,1],[57,80,71.0417,0.1979,22,1],"
    "[60,80,71.25,0.1979,22,1],[63,80,71.4583,0.1979,22,1],[42,64,71.6667,1.5833,22,5],"
    "[48,64,71.875,0.5938,22,6],[57,80,72.0833,0.1979,22,1],[60,80,72.2917,0.1979,22,1],"
    "[63,80,72.5,0.1979,22,1],[57,80,72.7083,0.1979,22,1],[60,80,72.9167,0.1979,22,1],"
    "[63,80,73.125,0.1979,22,1],[44,64,73.3333,1.5833,23,5],[53,64,73.5417,0.5938,23,6],"
    "[59,80,73.75,0.1979,23,1],[60,80,73.9583,0.1979,23,1],[62,80,74.1667,0.1979,23,1],"
    "[59,80,74.375,0.1979,23,1],[60,80,74.5833,0.1979,23,1],[62,80,74.7917,0.1979,23,1],"
    "[44,64,75.0,1.5833,23,5],[53,64,75.2083,0.5938,23,6],[59,80,75.4167,0.1979,23,1],"
    "[60,80,75.625,0.1979,23,1],[62,80,75.8333,0.1979,23,1],[59,80,76.0417,0.1979,23,1],"
    "[60,80,76.25,0.1979,23,1],[62,80,76.4583,0.1979,23,1],[43,64,76.6667,1.5833,24,5],"
    "[53,64,76.875,0.5938,24,6],[55,80,77.0833,0.1979,24,1],[59,80,77.2917,0.1979,24,1],"
    "[62,80,77.5,0.1979,24,1],[55,80,77.7083,0.1979,24,1],[59,80,77.9167,0.1979,24,1],"
    "[62,80,78.125,0.1979,24,1],[43,64,78.3333,1.5833,24,5],[53,64,78.5417,0.5938,24,6],"
    "[55,80,78.75,0.1979,24,1],[59,80,78.9583,0.1979,24,1],[62,80,79.1667,0.1979,24,1],"
    "[55,80,79.375,0.1979,24,1],[59,80,79.5833,0.1979,24,1],[62,80,79.7917,0.1979,24,1],"
    "[43,64,80.0,1.5833,25,5],[52,64,80.2083,0.5938,25,6],[55,80,80.4167,0.1979,25,1],"
    "[60,80,80.625,0.1979,25,1],[64,80,80.8333,0.1979,25,1],[55,80,81.0417,0.1979,25,1],"
    "[60,80,81.25,0.1979,25,1],[64,80,81.4583,0.1979,25,1],[43,64,81.6667,1.5833,25,5],"
    "[52,64,81.875,0.5938,25,6],[55,80,82.0833,0.1979,25,1],[60,80,82.2917,0.1979,25,1],"
    "[64,80,82.5,0.1979,25,1],[55,80,82.7083,0.1979,25,1],[60,80,82.9167,0.1979,25,1],"
    "[64,80,83.125,0.1979,25,1],[43,64,83.3333,1.5833,26,5],[50,64,83.5417,0.5938,26,6],"
    "[55,80,83.75,0.1979,26,1],[59,80,83.9583,0.1979,26,1],[65,80,84.1667,0.1979,26,1],"
    "[55,80,84.375,0.1979,26,1],[59,80,84.5833,0.1979,26,1],[65,80,84.7917,0.1979,26,1],"
    "[43,64,85.0,1.5833,26,5],[50,64,85.2083,0.5938,26,6],[55,80,85.4167,0.1979,26,1],"
    "[59,80,85.625,0.1979,26,1],[65,80,85.8333,0.1979,26,1],[55,80,86.0417,0.1979,26,1],"
    "[59,80,86.25,0.1979,26,1],[65,80,86.4583,0.1979,26,1],[43,64,86.6667,1.5833,27,5],"
    "[51,64,86.875,0.5938,27,6],[57,80,87.0833,0.1979,27,1],[60,80,87.2917,0.1979,27,1],"
    "[66,80,87.5,0.1979,27,1],[57,80,87.7083,0.1979,27,1],[60,80,87.9167,0.1979,27,1],"
    "[66,80,88.125,0.1979,27,1],[43,64,88.3333,1.5833,27,5],[51,64,88.5417,0.5938,27,6],"
    "[57,80,88.75,0.1979,27,1],[60,80,88.9583,0.1979,27,1],[66,80,89.1667,0.1979,27,1],"
    "[57,80,89.375,0.1979,27,1],[60,80,89.5833,0.1979,27,1],[66,80,89.7917,0.1979,27,1],"
    "[43,64,90.0,1.5833,28,5],[52,64,90.2083,0.5938,28,6],[55,80,90.4167,0.1979,28,1],"
    "[60,80,90.625,0.1979,28,1],[67,80,90.8333,0.1979,28,1],[55,80,91.0417,0.1979,28,1],"
    "[60,80,91.25,0.1979,28,1],[67,80,91.4583,0.1979,28,1],[43,64,91.6667,1.5833,28,5],"
    "[52,64,91.875,0.5938,28,6],[55,80,92.0833,0.1979,28,1],[60,80,92.2917,0.1979,28,1],"
    "[67,80,92.5,0.1979,28,1],[55,80,92.7083,0.1979,28,1],[60,80,92.9167,0.1979,28,1],"
    "[67,80,93.125,0.1979,28,1],[43,64,93.3333,1.5833,29,5],[50,64,93.5417,0.5938,29,6],"
    "[55,80,93.75,0.1979,29,1],[60,80,93.9583,0.1979,29,1],[65,80,94.1667,0.1979,29,1],"
    "[55,80,94.375,0.1979,29,1],[60,80,94.5833,0.1979,29,1],[65,80,94.7917,0.1979,29,1],"
    "[43,64,95.0,1.5833,29,5],[50,64,95.2083,0.5938,29,6],[55,80,95.4167,0.1979,29,1],"
    "[60,80,95.625,0.1979,29,1],[65,80,95.8333,0.1979,29,1],[55,80,96.0417,0.1979,29,1],"
    "[60,80,96.25,0.1979,29,1],[65,80,96.4583,0.1979,29,1],[43,64,96.6667,1.5833,30,5],"
    "[50,64,96.875,0.5938,30,6],[55,80,97.0833,0.1979,30,1],[59,80,97.2917,0.1979,30,1],"
    "[65,80,97.5,0.1979,30,1],[55,80,97.7083,0.1979,30,1],[59,80,97.9167,0.1979,30,1],"
    "[65,80,98.125,0.1979,30,1],[43,64,98.3333,1.5833,30,5],[50,64,98.5417,0.5938,30,6],"
    "[55,80,98.75,0.1979,30,1],[59,80,98.9583,0.1979,30,1],[65,80,99.1667,0.1979,30,1],"
    "[55,80,99.375,0.1979,30,1],[59,80,99.5833,0.1979,30,1],[65,80,99.7917,0.1979,30,1],"
    "[36,64,100.0,1.5833,31,5],[48,64,100.2083,0.5938,31,6],[55,80,100.4167,0.1979,31,1],"
    "[58,80,100.625,0.1979,31,1],[64,80,100.8333,0.1979,31,1],[55,80,101.0417,0.1979,31,1],"
    "[58,80,101.25,0.1979,31,1],[64,80,101.4583,0.1979,31,1],[36,64,101.6667,1.5833,31,5],"
    "[48,64,101.875,0.5938,31,6],[55,80,102.0833,0.1979,31,1],[58,80,102.2917,0.1979,31,1],"
    "[64,80,102.5,0.1979,31,1],[55,80,102.7083,0.1979,31,1],[58,80,102.9167,0.1979,31,1],"
    "[64,80,103.125,0.1979,31,1],[36,64,103.3333,1.5833,32,5],[48,64,103.5417,0.5938,32,6],"
    "[53,80,103.75,0.1979,32,1],[57,80,103.9583,0.1979,32,1],[60,80,104.1667,0.1979,32,1],"
    "[65,80,104.375,0.1979,32,1],[60,80,104.5833,0.1979,32,1],[57,80,104.7917,0.1979,32,1],"
    "[60,80,105.0,0.1979,32,1],[57,80,105.2083,0.1979,32,1],[53,80,105.4167,0.1979,32,1],"
    "[57,80,105.625,0.1979,32,1],[53,80,105.8333,0.1979,32,1],[50,80,106.0417,0.1979,32,1],"
    "[53,80,106.25,0.1979,32,1],[50,80,106.4583,0.1979,32,1],[36,64,116.3636,1.7273,33,5],"
    "[47,64,116.5909,0.6477,33,6],[67,80,116.8182,0.2159,33,1],[71,80,117.0455,0.2159,33,1],"
    "[74,80,117.2727,0.2159,33,1],[77,80,117.5,0.2159,33,1],[74,80,117.7273,0.2159,33,1],"
    "[71,80,117.9545,0.2159,33,1],[74,80,118.1818,0.2159,33,1],[71,80,118.4091,0.2159,33,1],"
    "[67,80,118.6364,0.2159,33,1],[71,80,118.8636,0.2159,33,1],[62,80,119.0909,0.2159,33,1],"
    "[65,80,119.3182,0.2159,33,1],[64,80,119.5455,0.2159,33,1],[62,80,119.7727,0.2159,33,1],"
    "[64,80,120.0,3.4545,34,1],[36,64,120.0,3.4545,34,5],[67,80,123.6364,3.4545,34,1],"
    "[72,80,123.6364,3.4545,34,1],[48,64,123.6364,3.4545,34,5]]"
)

# ──────────────────────────────────────────────────────────────────────────────
# 21 MIDI Dimensions
# ──────────────────────────────────────────────────────────────────────────────
_DIMS_21: list[dict[str, str]] = [
    {"id": "notes",            "label": "Notes",          "group": "core",   "color": "#00d4ff", "desc": "note_on/note_off — the musical content itself"},
    {"id": "pitch_bend",       "label": "Pitch Bend",     "group": "expr",   "color": "#7c6cff", "desc": "pitchwheel — semitone-accurate pitch deviation"},
    {"id": "channel_pressure", "label": "Ch. Pressure",   "group": "expr",   "color": "#9d8cff", "desc": "aftertouch — mono channel pressure"},
    {"id": "poly_pressure",    "label": "Poly Aftertouch","group": "expr",   "color": "#b8a8ff", "desc": "polytouch — per-note polyphonic aftertouch"},
    {"id": "cc_modulation",    "label": "Modulation",     "group": "cc",     "color": "#ff6b9d", "desc": "CC 1 — modulation wheel depth"},
    {"id": "cc_volume",        "label": "Volume",         "group": "cc",     "color": "#ff8c42", "desc": "CC 7 — channel volume level"},
    {"id": "cc_pan",           "label": "Pan",            "group": "cc",     "color": "#ffd700", "desc": "CC 10 — stereo pan position"},
    {"id": "cc_expression",    "label": "Expression",     "group": "cc",     "color": "#00ff87", "desc": "CC 11 — expression controller"},
    {"id": "cc_sustain",       "label": "Sustain Pedal",  "group": "cc",     "color": "#00d4ff", "desc": "CC 64 — damper/sustain pedal"},
    {"id": "cc_portamento",    "label": "Portamento",     "group": "cc",     "color": "#66e0ff", "desc": "CC 65 — portamento on/off"},
    {"id": "cc_sostenuto",     "label": "Sostenuto",      "group": "cc",     "color": "#99eaff", "desc": "CC 66 — sostenuto pedal"},
    {"id": "cc_soft_pedal",    "label": "Soft Pedal",     "group": "cc",     "color": "#aaeeff", "desc": "CC 67 — soft pedal (una corda)"},
    {"id": "cc_reverb",        "label": "Reverb Send",    "group": "fx",     "color": "#e879f9", "desc": "CC 91 — reverb send level"},
    {"id": "cc_chorus",        "label": "Chorus Send",    "group": "fx",     "color": "#c084fc", "desc": "CC 93 — chorus send level"},
    {"id": "cc_other",         "label": "Other CC",       "group": "fx",     "color": "#a78bfa", "desc": "All remaining CC numbers"},
    {"id": "program_change",   "label": "Program/Patch",  "group": "meta",   "color": "#fb923c", "desc": "program_change — instrument/patch select"},
    {"id": "tempo_map",        "label": "Tempo Map",      "group": "meta",   "color": "#f87171", "desc": "set_tempo meta events (non-independent)"},
    {"id": "time_signatures",  "label": "Time Signatures","group": "meta",   "color": "#fbbf24", "desc": "time_signature meta events (non-independent)"},
    {"id": "key_signatures",   "label": "Key Signatures", "group": "meta",   "color": "#a3e635", "desc": "key_signature meta events"},
    {"id": "markers",          "label": "Markers",        "group": "meta",   "color": "#34d399", "desc": "marker, cue, text, lyrics, copyright events"},
    {"id": "track_structure",  "label": "Track Structure","group": "meta",   "color": "#94a3b8", "desc": "track_name, sysex, unknown meta (non-independent)"},
]

# ──────────────────────────────────────────────────────────────────────────────
# Commit graph definition
# dagX: column (0=lower-register, 1=main, 2=upper-register)
# dagY: row (0=top)
# filter: {minM, maxM, voices[]} or null for no notes
# dims: dimension IDs active/modified in this commit
# dimAct: activity level per dimension (0-4)
# ──────────────────────────────────────────────────────────────────────────────
_COMMITS: list[dict[str, object]] = [
    {
        "id": "c0", "sha": "0000000", "branch": "main",
        "label": "muse init",
        "message": "Initial commit — muse init --domain midi",
        "command": "muse init --domain midi",
        "output": "✓ Initialized Muse repository\n  Domain    : midi\n  Dimensions: 21\n  Location  : .muse/\n  Tracking  : muse-work/",
        "parents": [],
        "dagX": 1, "dagY": 0,
        "filter": None,
        "newVoices": [],
        "newMeasures": [],
        "dims": [],
        "dimAct": {},
        "stats": "0 notes · 0 dimensions",
        "noteCount": 0,
    },
    {
        "id": "c1", "sha": "a1b2c3d", "branch": "feat/lower-register",
        "label": "bass + inner\nbars 1–12",
        "message": "feat: bass and inner voices, bars 1–12",
        "command": 'muse commit -m "feat: bass and inner voices, bars 1–12"',
        "output": "✓ [feat/lower-register a1b2c3d]\n  48 notes added\n  Dimensions modified: notes, tempo_map,\n    time_signatures, track_structure\n  Key detected: C major",
        "parents": ["c0"],
        "dagX": 0, "dagY": 1,
        "filter": {"minM": 1, "maxM": 12, "voices": [5, 6]},
        "newVoices": [5, 6],
        "newMeasures": [1, 12],
        "dims": ["notes", "tempo_map", "time_signatures", "track_structure"],
        "dimAct": {"notes": 3, "tempo_map": 2, "time_signatures": 2, "track_structure": 1},
        "stats": "+48 notes · 4 dimensions",
        "noteCount": 48,
    },
    {
        "id": "c2", "sha": "b3c4d5e", "branch": "feat/lower-register",
        "label": "lower voices\nbars 13–24",
        "message": "feat: bass and inner voices extended, bars 13–24",
        "command": 'muse commit -m "feat: bass and inner voices extended, bars 13–24"',
        "output": "✓ [feat/lower-register b3c4d5e]\n  40 notes added\n  Dimensions modified: notes, cc_sustain,\n    cc_volume\n  Chord progression: Fm → C7 → Dm",
        "parents": ["c1"],
        "dagX": 0, "dagY": 2,
        "filter": {"minM": 1, "maxM": 24, "voices": [5, 6]},
        "newVoices": [5, 6],
        "newMeasures": [13, 24],
        "dims": ["notes", "cc_sustain", "cc_volume"],
        "dimAct": {"notes": 3, "cc_sustain": 2, "cc_volume": 1},
        "stats": "+40 notes · 3 dimensions",
        "noteCount": 88,
    },
    {
        "id": "c3", "sha": "c4d5e6f", "branch": "feat/lower-register",
        "label": "lower voices\nbars 25–34 + FX",
        "message": "feat: complete lower register + reverb + expression",
        "command": 'muse commit -m "feat: complete lower register + reverb + expression"',
        "output": "✓ [feat/lower-register c4d5e6f]\n  42 notes added\n  Dimensions modified: notes, cc_sustain,\n    cc_reverb, cc_expression, markers\n  Bass descends to C2 (MIDI 36) — full range",
        "parents": ["c2"],
        "dagX": 0, "dagY": 3,
        "filter": {"minM": 1, "maxM": 34, "voices": [5, 6]},
        "newVoices": [5, 6],
        "newMeasures": [25, 34],
        "dims": ["notes", "cc_sustain", "cc_reverb", "cc_expression", "markers"],
        "dimAct": {"notes": 3, "cc_sustain": 2, "cc_reverb": 2, "cc_expression": 3, "markers": 1},
        "stats": "+42 notes · 5 dimensions",
        "noteCount": 130,
    },
    {
        "id": "c4", "sha": "d5e6f7a", "branch": "feat/upper-register",
        "label": "treble arpeggios\nbars 1–12",
        "message": "feat: treble arpeggios, bars 1–12",
        "command": 'muse commit -m "feat: treble arpeggios, bars 1–12"',
        "output": "✓ [feat/upper-register d5e6f7a]\n  144 notes added\n  Dimensions modified: notes, cc_volume,\n    program_change\n  Voice 1: soprano arpeggios reach A5 (MIDI 81)",
        "parents": ["c0"],
        "dagX": 2, "dagY": 1,
        "filter": {"minM": 1, "maxM": 12, "voices": [1]},
        "newVoices": [1],
        "newMeasures": [1, 12],
        "dims": ["notes", "cc_volume", "program_change"],
        "dimAct": {"notes": 4, "cc_volume": 2, "program_change": 1},
        "stats": "+144 notes · 3 dimensions",
        "noteCount": 144,
    },
    {
        "id": "c5", "sha": "e6f7a8b", "branch": "feat/upper-register",
        "label": "arpeggios\nbars 13–24",
        "message": "feat: treble arpeggios, bars 13–24 + modulation",
        "command": 'muse commit -m "feat: treble arpeggios, bars 13–24 + modulation"',
        "output": "✓ [feat/upper-register e6f7a8b]\n  120 notes added\n  Dimensions modified: notes, cc_modulation,\n    cc_expression, key_signatures\n  Development section — chromatic tensions",
        "parents": ["c4"],
        "dagX": 2, "dagY": 2,
        "filter": {"minM": 1, "maxM": 24, "voices": [1]},
        "newVoices": [1],
        "newMeasures": [13, 24],
        "dims": ["notes", "cc_modulation", "cc_expression", "key_signatures"],
        "dimAct": {"notes": 4, "cc_modulation": 2, "cc_expression": 3, "key_signatures": 1},
        "stats": "+120 notes · 4 dimensions",
        "noteCount": 264,
    },
    {
        "id": "c6", "sha": "f7a8b9c", "branch": "feat/upper-register",
        "label": "coda\nbars 25–34",
        "message": "feat: coda arpeggios bars 25–34 + final dynamics",
        "command": 'muse commit -m "feat: coda arpeggios bars 25–34 + final dynamics"',
        "output": "✓ [feat/upper-register f7a8b9c]\n  139 notes added\n  Dimensions modified: notes, cc_expression,\n    cc_soft_pedal, markers\n  Coda: bars 33–34 hold final C major chord",
        "parents": ["c5"],
        "dagX": 2, "dagY": 3,
        "filter": {"minM": 1, "maxM": 34, "voices": [1]},
        "newVoices": [1],
        "newMeasures": [25, 34],
        "dims": ["notes", "cc_expression", "cc_soft_pedal", "markers"],
        "dimAct": {"notes": 4, "cc_expression": 3, "cc_soft_pedal": 2, "markers": 2},
        "stats": "+139 notes · 4 dimensions",
        "noteCount": 403,
    },
    {
        "id": "c7", "sha": "9a0b1c2", "branch": "main",
        "label": "muse merge\nPrelude complete ✓",
        "message": "merge: unite lower and upper registers — Prelude BWV 846 complete",
        "command": "muse merge feat/lower-register feat/upper-register",
        "output": "✓ [main 9a0b1c2] merge: Prelude BWV 846 complete\n  Auto-merged: notes (no pitch conflicts —\n    registers non-overlapping)\n  Merged dimensions: 10 / 21\n  533 notes · 2:07 duration · Key: C major",
        "parents": ["c3", "c6"],
        "dagX": 1, "dagY": 4,
        "filter": {"minM": 1, "maxM": 34, "voices": [1, 5, 6]},
        "newVoices": [1, 5, 6],
        "newMeasures": [1, 34],
        "dims": [
            "notes", "tempo_map", "time_signatures", "track_structure",
            "cc_sustain", "cc_volume", "cc_expression", "cc_reverb",
            "cc_modulation", "cc_soft_pedal", "markers", "program_change",
            "key_signatures",
        ],
        "dimAct": {
            "notes": 4, "tempo_map": 2, "time_signatures": 2, "track_structure": 1,
            "cc_sustain": 2, "cc_volume": 2, "cc_expression": 3, "cc_reverb": 2,
            "cc_modulation": 2, "cc_soft_pedal": 2, "markers": 2, "program_change": 1,
            "key_signatures": 1,
        },
        "stats": "533 notes · 13 dimensions · 2:07",
        "noteCount": 533,
    },
]


def render_midi_demo() -> str:
    """Generate the complete self-contained MIDI demo HTML page."""
    notes_json = _BACH_NOTES_JSON
    commits_json = json.dumps(_COMMITS, separators=(",", ":"))
    dims_json = json.dumps(_DIMS_21, separators=(",", ":"))

    html = _HTML_TEMPLATE
    html = html.replace("__NOTES_JSON__", notes_json)
    html = html.replace("__COMMITS_JSON__", commits_json)
    html = html.replace("__DIMS_JSON__", dims_json)
    return html


# ──────────────────────────────────────────────────────────────────────────────
# HTML template  (no Python f-strings — JS braces are literal)
# ──────────────────────────────────────────────────────────────────────────────
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bach BWV 846 × Muse VCS — 21-Dimensional MIDI Demo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/tone@14.7.77/build/Tone.js"></script>
<style>
:root {
  --bg: #07090f;
  --bg2: #0c0f1a;
  --bg3: #111627;
  --surface: rgba(255,255,255,0.035);
  --surface2: rgba(255,255,255,0.06);
  --border: rgba(255,255,255,0.07);
  --border2: rgba(255,255,255,0.12);
  --text: #e2e8f0;
  --muted: #64748b;
  --dim: #475569;
  --cyan: #00d4ff;
  --purple: #7c6cff;
  --gold: #ffd700;
  --green: #00ff87;
  --pink: #ff6b9d;
  --orange: #ff8c42;
  --font: 'Inter', sans-serif;
  --mono: 'JetBrains Mono', monospace;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.6;
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── Particles canvas ── */
#particles-canvas {
  position: fixed; top: 0; left: 0;
  width: 100%; height: 100%;
  pointer-events: none; z-index: 0;
  opacity: 0.4;
}

/* ── NAV ── */
.nav {
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 28px;
  background: rgba(7,9,15,0.85);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border);
}
.nav-brand {
  display: flex; align-items: center; gap: 10px;
  font-family: var(--mono); font-size: 13px;
  color: var(--cyan); text-decoration: none;
  letter-spacing: 0.02em;
}
.nav-brand .sep { color: var(--muted); }
.nav-links { display: flex; gap: 20px; }
.nav-links a {
  color: var(--muted); text-decoration: none; font-size: 12px;
  letter-spacing: 0.05em; text-transform: uppercase;
  transition: color 0.2s;
}
.nav-links a:hover { color: var(--text); }
.badge {
  background: rgba(0,212,255,0.1); color: var(--cyan);
  border: 1px solid rgba(0,212,255,0.2);
  padding: 2px 8px; border-radius: 20px;
  font-size: 10px; font-family: var(--mono);
  letter-spacing: 0.05em;
}

/* ── HERO ── */
.hero {
  position: relative; z-index: 1;
  text-align: center;
  padding: 60px 28px 44px;
  background: radial-gradient(ellipse 80% 60% at 50% 0%, rgba(124,108,255,0.12) 0%, transparent 70%);
}
.hero-eyebrow {
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.15em;
  text-transform: uppercase; color: var(--cyan); margin-bottom: 14px;
}
.hero h1 {
  font-size: clamp(28px, 5vw, 52px);
  font-weight: 300; letter-spacing: -0.02em;
  line-height: 1.15; margin-bottom: 16px;
}
.hero h1 em { font-style: normal; color: var(--cyan); }
.hero h1 strong { font-weight: 600; }
.hero-sub {
  color: var(--muted); font-size: 15px; max-width: 580px;
  margin: 0 auto 32px;
}
.hero-pills {
  display: flex; flex-wrap: wrap; justify-content: center;
  gap: 8px; margin-bottom: 36px;
}
.pill {
  padding: 5px 12px; border-radius: 20px;
  font-size: 11px; font-family: var(--mono);
  border: 1px solid var(--border2);
  background: var(--surface);
  color: var(--muted);
}
.pill.cyan  { border-color: rgba(0,212,255,0.3);   color: var(--cyan);   background: rgba(0,212,255,0.07); }
.pill.purple{ border-color: rgba(124,108,255,0.3); color: var(--purple); background: rgba(124,108,255,0.07); }
.pill.gold  { border-color: rgba(255,215,0,0.3);   color: var(--gold);   background: rgba(255,215,0,0.07); }
.pill.green { border-color: rgba(0,255,135,0.3);   color: var(--green);  background: rgba(0,255,135,0.07); }
.hero-actions { display: flex; justify-content: center; gap: 12px; flex-wrap: wrap; }
.btn {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 9px 20px; border-radius: 8px;
  font-size: 13px; font-weight: 500; cursor: pointer;
  border: none; transition: all 0.2s; text-decoration: none;
  font-family: var(--font);
}
.btn-primary {
  background: var(--cyan); color: #07090f;
}
.btn-primary:hover { background: #33ddff; transform: translateY(-1px); }
.btn-ghost {
  background: var(--surface2); color: var(--text);
  border: 1px solid var(--border2);
}
.btn-ghost:hover { background: rgba(255,255,255,0.09); }
.btn-sm { padding: 6px 14px; font-size: 12px; }
.btn-icon { font-size: 15px; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── DEMO WRAPPER ── */
.demo-wrapper {
  position: relative; z-index: 1;
  max-width: 1400px; margin: 0 auto;
  padding: 0 16px 40px;
}

/* ── SECTION HEADING ── */
.section-title {
  font-size: 10px; font-family: var(--mono);
  letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 12px;
  display: flex; align-items: center; gap: 8px;
}
.section-title::before {
  content: ''; display: block;
  width: 16px; height: 1px;
  background: var(--border2);
}

/* ── MAIN DEMO GRID ── */
.main-demo {
  display: grid;
  grid-template-columns: 220px 1fr 220px;
  grid-template-rows: auto;
  gap: 12px;
  margin-bottom: 12px;
}

/* ── PANEL ── */
.panel {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.panel-header {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  background: rgba(255,255,255,0.015);
}
.panel-title {
  font-size: 10px; font-family: var(--mono);
  letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted);
}
.panel-body { padding: 0; }

/* ── DAG PANEL ── */
#dag-container {
  height: 420px;
  overflow: hidden;
  padding: 8px 0;
}
#dag-container svg { width: 100%; height: 100%; }

/* ── PIANO ROLL PANEL ── */
#piano-roll-wrap {
  height: 420px;
  overflow-x: auto;
  overflow-y: hidden;
  position: relative;
  cursor: crosshair;
  background: #080c16;
}
#piano-roll-wrap::-webkit-scrollbar { height: 4px; }
#piano-roll-wrap::-webkit-scrollbar-track { background: var(--bg2); }
#piano-roll-wrap::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }
#piano-roll-svg { display: block; }
.note-rect { transition: opacity 0.3s; }
.note-new { filter: brightness(1.3); }

/* ── DIM PANEL ── */
.dim-list {
  height: 420px;
  overflow-y: auto;
  padding: 4px 0;
}
.dim-list::-webkit-scrollbar { width: 4px; }
.dim-list::-webkit-scrollbar-thumb { background: var(--border2); }
.dim-row {
  display: flex; align-items: center;
  padding: 5px 12px; gap: 8px;
  transition: background 0.2s; cursor: default;
}
.dim-row:hover { background: var(--surface); }
.dim-dot {
  width: 8px; height: 8px; border-radius: 50%;
  flex-shrink: 0;
  background: var(--dim);
  transition: background 0.3s, box-shadow 0.3s;
}
.dim-row.active .dim-dot {
  box-shadow: 0 0 8px currentColor;
}
.dim-name {
  font-family: var(--mono); font-size: 10px;
  color: var(--muted); flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  transition: color 0.3s;
}
.dim-row.active .dim-name { color: var(--text); }
.dim-bar-wrap {
  width: 48px; height: 4px;
  background: rgba(255,255,255,0.05);
  border-radius: 2px; overflow: hidden;
}
.dim-bar {
  height: 100%; border-radius: 2px;
  width: 0; transition: width 0.4s ease, background 0.3s;
}
.dim-group-label {
  padding: 8px 12px 2px;
  font-size: 9px; font-family: var(--mono);
  text-transform: uppercase; letter-spacing: 0.12em;
  color: var(--dim);
}

/* ── CONTROLS ── */
.controls-bar {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 20px;
  display: flex; align-items: center; gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.ctrl-group { display: flex; align-items: center; gap: 8px; }
.ctrl-btn {
  width: 36px; height: 36px; border-radius: 8px;
  background: var(--surface); border: 1px solid var(--border2);
  color: var(--text); font-size: 14px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.ctrl-btn:hover { background: var(--surface2); }
.ctrl-btn.active { background: var(--cyan); color: #07090f; border-color: var(--cyan); }
.ctrl-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.ctrl-play {
  width: 44px; height: 44px; border-radius: 50%;
  background: var(--cyan); border: none; color: #07090f;
  font-size: 18px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 0 20px rgba(0,212,255,0.3);
  transition: all 0.15s;
}
.ctrl-play:hover { background: #33ddff; transform: scale(1.05); }
.ctrl-play.playing { background: var(--pink); box-shadow: 0 0 20px rgba(255,107,157,0.3); }
.ctrl-sep { width: 1px; height: 28px; background: var(--border); }
.ctrl-timeline {
  flex: 1; min-width: 120px;
  display: flex; align-items: center; gap: 10px;
}
.ctrl-time {
  font-family: var(--mono); font-size: 11px; color: var(--muted);
  white-space: nowrap;
}
.progress-track {
  flex: 1; height: 4px; background: var(--surface2);
  border-radius: 2px; cursor: pointer; position: relative;
}
.progress-fill {
  height: 100%; background: var(--cyan); border-radius: 2px;
  width: 0; transition: width 0.1s linear;
}
.audio-status {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--muted); font-family: var(--mono);
}
.audio-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--dim); transition: background 0.3s;
}
.audio-dot.ready { background: var(--green); box-shadow: 0 0 8px var(--green); }
.audio-dot.loading { background: var(--gold); animation: pulse 1s infinite; }
.commit-info {
  flex: 1; min-width: 180px;
}
.commit-sha {
  font-family: var(--mono); font-size: 10px; color: var(--cyan);
}
.commit-msg {
  font-size: 11px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-width: 260px;
}

/* ── COMMAND LOG ── */
.cmd-log-panel {
  background: #060810;
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 12px; overflow: hidden;
}
.cmd-log-header {
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
  background: rgba(255,255,255,0.02);
}
.terminal-dots { display: flex; gap: 5px; }
.terminal-dots span {
  width: 10px; height: 10px; border-radius: 50%;
}
.dot-red   { background: #ff5f57; }
.dot-yellow{ background: #febc2e; }
.dot-green { background: #28c840; }
.cmd-log-title {
  font-family: var(--mono); font-size: 10px;
  color: var(--muted); letter-spacing: 0.08em;
  flex: 1; text-align: center;
}
.cmd-log-body {
  padding: 14px 20px;
  font-family: var(--mono); font-size: 12px;
  min-height: 110px; max-height: 160px;
  overflow-y: auto;
}
.cmd-log-body::-webkit-scrollbar { width: 4px; }
.cmd-log-body::-webkit-scrollbar-thumb { background: var(--border2); }
.log-line { line-height: 1.7; }
.log-prompt { color: var(--green); }
.log-cmd    { color: var(--text); }
.log-out    { color: var(--muted); white-space: pre; }
.log-cursor {
  display: inline-block; width: 8px; height: 13px;
  background: var(--cyan); vertical-align: middle;
  animation: blink 1s step-end infinite;
}

/* ── HEATMAP ── */
.heatmap-panel {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 40px; overflow: hidden;
}
.heatmap-body {
  padding: 16px 20px;
  overflow-x: auto;
}
#heatmap-svg { display: block; }

/* ── CLI REFERENCE ── */
.cli-section {
  max-width: 1400px; margin: 0 auto;
  padding: 0 16px 80px;
}
.cli-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 12px; margin-top: 20px;
}
.cmd-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 12px; padding: 16px 18px;
  transition: border-color 0.2s;
}
.cmd-card:hover { border-color: var(--border2); }
.cmd-name {
  font-family: var(--mono); font-size: 13px;
  color: var(--cyan); margin-bottom: 4px;
}
.cmd-desc {
  font-size: 12px; color: var(--muted); margin-bottom: 10px;
}
.cmd-flags { display: flex; flex-direction: column; gap: 4px; }
.cmd-flag {
  display: flex; gap: 8px; align-items: baseline;
}
.flag-name {
  font-family: var(--mono); font-size: 10px;
  color: var(--purple); white-space: nowrap;
}
.flag-desc {
  font-size: 11px; color: var(--dim);
}
.cmd-return {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid var(--border);
  font-size: 11px; color: var(--dim);
}
.cmd-return span {
  font-family: var(--mono); color: var(--gold);
}

/* ── FOOTER ── */
footer {
  border-top: 1px solid var(--border);
  padding: 24px 28px;
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 12px;
  font-size: 11px; color: var(--dim);
}
footer a { color: var(--muted); text-decoration: none; }
footer a:hover { color: var(--text); }

/* ── ANIMATIONS ── */
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
@keyframes fadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
.fade-in { animation: fadeIn 0.4s ease forwards; }

/* ── RESPONSIVE ── */
@media(max-width: 900px) {
  .main-demo {
    grid-template-columns: 1fr;
  }
  #dag-container, .dim-list { height: 200px; }
}
</style>
</head>
<body>

<canvas id="particles-canvas"></canvas>

<!-- ── NAV ── -->
<nav class="nav">
  <a class="nav-brand" href="index.html">
    <span>muse</span><span class="sep">/</span><span>midi-demo</span>
  </a>
  <div class="nav-links">
    <a href="index.html">Registry</a>
    <a href="demo.html">VCS Demo</a>
    <a href="https://github.com/cgcardona/muse" target="_blank">GitHub</a>
  </div>
  <span class="badge">v0.1.2</span>
</nav>

<!-- ── HERO ── -->
<section class="hero">
  <div class="hero-eyebrow">Muse VCS · MIDI Domain · 21-Dimensional Version Control</div>
  <h1>
    <em>Bach</em> · <strong>Prelude No. 1 in C Major</strong><br>
    BWV 846 · Well-Tempered Clavier
  </h1>
  <p class="hero-sub">
    Watch Bach's Prelude built commit-by-commit across two parallel branches —
    then merged automatically using Muse's 21-dimensional MIDI diff engine.
    533 authentic notes. Real piano audio. Zero conflicts.
  </p>
  <div class="hero-pills">
    <span class="pill cyan">533 notes · 34 bars</span>
    <span class="pill purple">21 MIDI dimensions</span>
    <span class="pill gold">2 branches · 1 merge</span>
    <span class="pill green">Salamander Grand Piano</span>
    <span class="pill">Bach (1685–1750) · Public domain</span>
    <span class="pill">music21 corpus</span>
  </div>
  <div class="hero-actions">
    <button class="btn btn-primary" id="btn-init-audio">
      <span class="btn-icon">🎹</span> Load Piano &amp; Begin
    </button>
    <a class="btn btn-ghost" href="#cli-reference">
      <span class="btn-icon">📖</span> CLI Reference
    </a>
    <a class="btn btn-ghost" href="index.html">
      <span class="btn-icon">←</span> Landing Page
    </a>
  </div>
</section>

<!-- ── MAIN DEMO ── -->
<div class="demo-wrapper">

  <!-- info bar -->
  <div class="section-title" style="margin-bottom:14px">
    Interactive Demo — click any commit to hear and see that state
  </div>

  <!-- 3-column grid -->
  <div class="main-demo">

    <!-- DAG -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Commit DAG</span>
        <span class="badge" style="font-size:9px" id="dag-branch-label">main</span>
      </div>
      <div id="dag-container"></div>
    </div>

    <!-- Piano Roll -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Piano Roll — 4 octaves (C2 → C6)</span>
        <div style="display:flex;gap:8px;align-items:center">
          <span class="pill cyan" style="font-size:9px">■ Treble arpeggios</span>
          <span class="pill purple" style="font-size:9px">■ Bass</span>
          <span class="pill gold" style="font-size:9px">■ Inner voice</span>
        </div>
      </div>
      <div id="piano-roll-wrap">
        <svg id="piano-roll-svg"></svg>
      </div>
    </div>

    <!-- 21-Dim Panel -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">21 MIDI Dimensions</span>
        <span id="dim-active-count" style="font-family:var(--mono);font-size:10px;color:var(--muted)">0 active</span>
      </div>
      <div class="dim-list" id="dim-list"></div>
    </div>

  </div>

  <!-- Controls -->
  <div class="controls-bar">
    <div class="ctrl-group">
      <button class="ctrl-btn" id="btn-first" title="First commit">⏮</button>
      <button class="ctrl-btn" id="btn-prev"  title="Previous commit">◀</button>
      <button class="ctrl-play" id="btn-play" title="Play / Pause">▶</button>
      <button class="ctrl-btn" id="btn-next"  title="Next commit">▶</button>
      <button class="ctrl-btn" id="btn-last"  title="Last commit">⏭</button>
    </div>
    <div class="ctrl-sep"></div>
    <div class="ctrl-timeline">
      <span class="ctrl-time" id="time-display">0:00</span>
      <div class="progress-track" id="progress-track">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
      <span class="ctrl-time" id="time-total">2:07</span>
    </div>
    <div class="ctrl-sep"></div>
    <div class="commit-info">
      <div class="commit-sha" id="commit-sha-disp">0000000</div>
      <div class="commit-msg" id="commit-msg-disp">Select a commit to begin</div>
    </div>
    <div class="ctrl-sep"></div>
    <div class="audio-status">
      <div class="audio-dot" id="audio-dot"></div>
      <span id="audio-label">Click "Load Piano"</span>
    </div>
  </div>

  <!-- Command Log -->
  <div class="cmd-log-panel">
    <div class="cmd-log-header">
      <div class="terminal-dots">
        <span class="dot-red"></span>
        <span class="dot-yellow"></span>
        <span class="dot-green"></span>
      </div>
      <div class="cmd-log-title">muse — MIDI repository</div>
    </div>
    <div class="cmd-log-body" id="cmd-log">
      <div class="log-line">
        <span class="log-prompt">$ </span>
        <span class="log-cmd">muse status</span>
      </div>
      <div class="log-line log-out">On branch main · 0 notes · Select a commit ↑</div>
      <div class="log-line"><span class="log-cursor"></span></div>
    </div>
  </div>

  <!-- Heatmap -->
  <div class="heatmap-panel">
    <div class="panel-header">
      <span class="panel-title">Dimension Activity Heatmap — Commits × Dimensions</span>
      <span style="font-size:10px;color:var(--muted);font-family:var(--mono)">
        darker = inactive · brighter = active
      </span>
    </div>
    <div class="heatmap-body">
      <svg id="heatmap-svg"></svg>
    </div>
  </div>

</div><!-- /demo-wrapper -->

<!-- ── CLI REFERENCE ── -->
<div class="cli-section" id="cli-reference">
  <div class="section-title">MIDI-Domain Commands — muse CLI Reference</div>
  <p style="color:var(--muted);font-size:13px;margin-bottom:8px">
    All standard VCS commands (commit, log, branch, merge, diff, …) work on MIDI files.
    The commands below are MIDI-specific additions provided by MidiPlugin.
  </p>
  <div class="cli-grid" id="cli-grid"></div>
</div>

<footer>
  <div>
    <strong style="color:var(--text)">Muse VCS</strong> · v0.1.2 ·
    Bach BWV 846 — public domain (Bach 1685–1750) ·
    Note data: <a href="https://github.com/cuthbertLab/music21" target="_blank">music21 corpus</a> (CC0)
  </div>
  <div>
    <a href="index.html">Landing Page</a> ·
    <a href="demo.html">VCS Demo</a> ·
    <a href="https://github.com/cgcardona/muse" target="_blank">GitHub</a>
  </div>
</footer>

<script>
// ═══════════════════════════════════════════════════════════════
// DATA (injected by render_midi_demo.py)
// ═══════════════════════════════════════════════════════════════
// [pitch_midi, velocity, start_sec, duration_sec, measure, voice]
const BACH_NOTES = __NOTES_JSON__;
const COMMITS    = __COMMITS_JSON__;
const DIMS_21    = __DIMS_JSON__;

const TOTAL_DURATION = 127.09;
const VOICE_COLOR = { 1: '#00d4ff', 5: '#7c6cff', 6: '#ffd700' };
const PITCH_MIN = 36, PITCH_MAX = 84;

// ═══════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════
const state = {
  commitIdx: 0,
  isPlaying: false,
  audioReady: false,
  audioLoading: false,
  playheadSec: 0,
  playStartWallClock: 0,
  playStartAudioSec: 0,
  rafId: null,
};

// ═══════════════════════════════════════════════════════════════
// PARTICLES BACKGROUND
// ═══════════════════════════════════════════════════════════════
(function initParticles() {
  const canvas = document.getElementById('particles-canvas');
  const ctx = canvas.getContext('2d');
  let W, H, particles;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function createParticles() {
    const count = Math.floor(W * H / 14000);
    particles = Array.from({ length: count }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 1.2 + 0.2,
      a: Math.random() * Math.PI * 2,
      speed: Math.random() * 0.15 + 0.03,
      opacity: Math.random() * 0.5 + 0.1,
    }));
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (const p of particles) {
      p.x += Math.cos(p.a) * p.speed;
      p.y += Math.sin(p.a) * p.speed;
      p.a += (Math.random() - 0.5) * 0.02;
      if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
      if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(120,180,255,${p.opacity})`;
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }

  resize();
  createParticles();
  draw();
  window.addEventListener('resize', () => { resize(); createParticles(); });
})();

// ═══════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════
function getCommit(idx) { return COMMITS[idx]; }

function getNotesForCommit(commit) {
  if (!commit.filter) return [];
  const f = commit.filter;
  return BACH_NOTES.filter(n =>
    n[4] >= f.minM && n[4] <= f.maxM &&
    f.voices.includes(n[5])
  );
}

function isNewNote(note, commit) {
  if (!commit.filter || !commit.newMeasures.length) return false;
  return note[4] >= commit.newMeasures[0] &&
         note[4] <= commit.newMeasures[1] &&
         commit.newVoices.includes(note[5]);
}

function fmtTime(s) {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2,'0')}`;
}

// ═══════════════════════════════════════════════════════════════
// PIANO ROLL
// ═══════════════════════════════════════════════════════════════
const PR = (() => {
  const KEYBOARD_W = 44;
  const NOTE_H     = 8;
  const PX_PER_SEC = 6.5;
  const TOTAL_W    = Math.ceil(TOTAL_DURATION * PX_PER_SEC) + KEYBOARD_W + 20;
  const TOTAL_H    = (PITCH_MAX - PITCH_MIN) * NOTE_H;
  const WHITE_NOTES = new Set([0,2,4,5,7,9,11]);

  const svg = d3.select('#piano-roll-svg')
    .attr('width', TOTAL_W)
    .attr('height', TOTAL_H);

  // Background
  svg.append('rect')
    .attr('width', TOTAL_W).attr('height', TOTAL_H)
    .attr('fill', '#08101c');

  // Octave lines & background stripes
  for (let pitch = PITCH_MIN; pitch <= PITCH_MAX; pitch++) {
    const sem = pitch % 12;
    const y = TOTAL_H - (pitch - PITCH_MIN + 1) * NOTE_H;
    if (!WHITE_NOTES.has(sem)) {
      svg.append('rect')
        .attr('x', KEYBOARD_W).attr('y', y)
        .attr('width', TOTAL_W - KEYBOARD_W).attr('height', NOTE_H)
        .attr('fill', 'rgba(0,0,0,0.25)');
    }
    if (sem === 0) {
      svg.append('line')
        .attr('x1', KEYBOARD_W).attr('x2', TOTAL_W)
        .attr('y1', y).attr('y2', y)
        .attr('stroke', 'rgba(255,255,255,0.08)').attr('stroke-width', 1);
    }
  }

  // Bar grid (every 2 bars for readability)
  const secPerBar = (60.0 / 66) * 4;
  for (let bar = 0; bar <= 34; bar += 2) {
    const x = KEYBOARD_W + bar * secPerBar * PX_PER_SEC;
    svg.append('line')
      .attr('x1', x).attr('x2', x)
      .attr('y1', 0).attr('y2', TOTAL_H)
      .attr('stroke', bar % 8 === 0 ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)')
      .attr('stroke-width', 1);
    if (bar % 4 === 0 && bar > 0) {
      svg.append('text')
        .attr('x', x + 2).attr('y', 9)
        .attr('font-size', 8).attr('fill', 'rgba(255,255,255,0.2)')
        .attr('font-family', 'JetBrains Mono, monospace')
        .text(`m${bar+1}`);
    }
  }

  // Piano keyboard (left edge)
  for (let pitch = PITCH_MIN; pitch < PITCH_MAX; pitch++) {
    const sem = pitch % 12;
    const isWhite = WHITE_NOTES.has(sem);
    const y = TOTAL_H - (pitch - PITCH_MIN + 1) * NOTE_H;
    svg.append('rect')
      .attr('x', 0).attr('y', y + 0.5)
      .attr('width', isWhite ? KEYBOARD_W - 2 : KEYBOARD_W * 0.62)
      .attr('height', NOTE_H - 1)
      .attr('rx', 1)
      .attr('fill', isWhite ? 'rgba(230,235,245,0.88)' : '#1c1c2e');
    // C note label
    if (sem === 0) {
      const oct = Math.floor(pitch / 12) - 1;
      svg.append('text')
        .attr('x', KEYBOARD_W - 5).attr('y', y + NOTE_H - 1)
        .attr('font-size', 7).attr('fill', '#555')
        .attr('text-anchor', 'end')
        .attr('font-family', 'JetBrains Mono, monospace')
        .text(`C${oct}`);
    }
  }

  // Notes group (rendered above keyboard)
  const notesG = svg.append('g').attr('transform', `translate(${KEYBOARD_W},0)`);

  // Playhead
  const playhead = svg.append('line')
    .attr('class', 'playhead')
    .attr('x1', KEYBOARD_W).attr('x2', KEYBOARD_W)
    .attr('y1', 0).attr('y2', TOTAL_H)
    .attr('stroke', 'rgba(255,255,255,0.7)')
    .attr('stroke-width', 1.5)
    .attr('opacity', 0);

  let currentCommit = null;

  function update(commit) {
    currentCommit = commit;
    const notes = getNotesForCommit(commit);

    notesG.selectAll('.note-rect')
      .data(notes, d => d[0] + '_' + d[2])
      .join(
        enter => enter.append('rect')
          .attr('class', 'note-rect')
          .attr('x', d => d[2] * PX_PER_SEC)
          .attr('y', d => TOTAL_H - (d[0] - PITCH_MIN + 1) * NOTE_H)
          .attr('width', d => Math.max(d[3] * PX_PER_SEC - 1, 2))
          .attr('height', NOTE_H - 1)
          .attr('rx', 1.5)
          .attr('fill', d => VOICE_COLOR[d[5]] || '#888')
          .attr('opacity', 0)
          .call(s => {
            s.transition().duration(350)
             .attr('opacity', d => isNewNote(d, commit) ? 0.95 : 0.55);
          }),
        update => update
          .transition().duration(250)
          .attr('opacity', d => isNewNote(d, commit) ? 0.95 : 0.55),
        exit => exit
          .transition().duration(200)
          .attr('opacity', 0)
          .remove()
      );

    // Scroll piano roll to show new notes
    if (commit.newMeasures && commit.newMeasures.length) {
      const targetSec = (commit.newMeasures[0] - 1) * secPerBar;
      const scrollX = Math.max(0, targetSec * PX_PER_SEC - 40);
      document.getElementById('piano-roll-wrap').scrollTo({ left: scrollX, behavior: 'smooth' });
    }
  }

  function setPlayhead(sec) {
    const x = KEYBOARD_W + sec * PX_PER_SEC;
    playhead.attr('x1', x).attr('x2', x).attr('opacity', sec > 0 ? 0.8 : 0);
    // Auto-scroll to follow playhead
    const wrap = document.getElementById('piano-roll-wrap');
    const wrapW = wrap.clientWidth;
    const relX  = x - wrap.scrollLeft;
    if (relX > wrapW * 0.75) {
      wrap.scrollLeft = x - wrapW * 0.25;
    } else if (relX < wrapW * 0.1) {
      wrap.scrollLeft = Math.max(0, x - 60);
    }
  }

  return { update, setPlayhead };
})();

// ═══════════════════════════════════════════════════════════════
// DAG
// ═══════════════════════════════════════════════════════════════
const DAG = (() => {
  const container = document.getElementById('dag-container');
  const W = container.clientWidth || 210;
  const H = 410;
  const NODE_R = 14;
  const ROWS = 5;
  const ROW_H = (H - 70) / ROWS;

  // Col x positions
  const COL = [W * 0.18, W * 0.5, W * 0.82];

  // Branch colors
  const BRANCH_COLOR = {
    'main':                  '#00ff87',
    'feat/lower-register':   '#7c6cff',
    'feat/upper-register':   '#00d4ff',
  };

  const svg = d3.select('#dag-container').append('svg')
    .attr('width', W).attr('height', H);

  // Gradient defs
  const defs = svg.append('defs');
  ['lower','upper','main'].forEach(name => {
    const g = defs.append('radialGradient')
      .attr('id', `glow-${name}`)
      .attr('cx','50%').attr('cy','50%').attr('r','50%');
    const col = name === 'main' ? '#00ff87' : name === 'lower' ? '#7c6cff' : '#00d4ff';
    g.append('stop').attr('offset','0%').attr('stop-color', col).attr('stop-opacity', 0.4);
    g.append('stop').attr('offset','100%').attr('stop-color', col).attr('stop-opacity', 0);
  });

  // Position function
  function pos(c) {
    return { x: COL[c.dagX], y: 35 + c.dagY * ROW_H };
  }

  // Branch label headers
  [
    {x: COL[0], label: 'feat/lower', color: '#7c6cff'},
    {x: COL[1], label: 'main',       color: '#00ff87'},
    {x: COL[2], label: 'feat/upper', color: '#00d4ff'},
  ].forEach(({x, label, color}) => {
    svg.append('text')
      .attr('x', x).attr('y', 14)
      .attr('text-anchor', 'middle')
      .attr('font-size', 8)
      .attr('font-family', 'JetBrains Mono, monospace')
      .attr('fill', color)
      .attr('letter-spacing', 1)
      .text(label);
  });

  // Draw edges
  COMMITS.forEach(c => {
    c.parents.forEach(pid => {
      const parent = COMMITS.find(p => p.id === pid);
      if (!parent) return;
      const p1 = pos(parent), p2 = pos(c);
      const sameCol = Math.abs(p1.x - p2.x) < 5;
      if (sameCol) {
        svg.append('line')
          .attr('x1', p1.x).attr('y1', p1.y)
          .attr('x2', p2.x).attr('y2', p2.y)
          .attr('stroke', BRANCH_COLOR[c.branch] || '#fff')
          .attr('stroke-width', 1.5)
          .attr('stroke-opacity', 0.25);
      } else {
        const my = (p1.y + p2.y) / 2;
        const path = `M${p2.x},${p2.y} C${p2.x},${my} ${p1.x},${my} ${p1.x},${p1.y}`;
        svg.append('path')
          .attr('d', path)
          .attr('fill', 'none')
          .attr('stroke', 'rgba(255,255,255,0.18)')
          .attr('stroke-width', 1.5)
          .attr('stroke-dasharray', '3,2');
      }
    });
  });

  // Node groups
  const nodeGs = svg.selectAll('.dag-node')
    .data(COMMITS)
    .join('g')
    .attr('class', 'dag-node')
    .attr('transform', c => `translate(${pos(c).x},${pos(c).y})`)
    .attr('cursor', 'pointer')
    .on('click', (e, d) => selectCommit(COMMITS.indexOf(d)));

  // Glow ring (shown when selected)
  nodeGs.append('circle')
    .attr('r', NODE_R + 8)
    .attr('class', 'node-glow')
    .attr('fill', d => {
      const n = d.branch === 'main' ? 'main' : d.branch.includes('lower') ? 'lower' : 'upper';
      return `url(#glow-${n})`;
    })
    .attr('opacity', 0);

  // Selection ring
  nodeGs.append('circle')
    .attr('r', NODE_R + 4)
    .attr('class', 'node-ring')
    .attr('fill', 'none')
    .attr('stroke', d => BRANCH_COLOR[d.branch] || '#fff')
    .attr('stroke-width', 1.5)
    .attr('opacity', 0);

  // Main circle
  nodeGs.append('circle')
    .attr('r', NODE_R)
    .attr('fill', d => {
      if (d.branch === 'main') return '#0d1f14';
      if (d.branch.includes('lower')) return '#12102a';
      return '#0a1c28';
    })
    .attr('stroke', d => BRANCH_COLOR[d.branch] || '#fff')
    .attr('stroke-width', 1.8);

  // SHA label
  nodeGs.append('text')
    .attr('text-anchor', 'middle').attr('dy', '0.35em')
    .attr('font-size', 8).attr('fill', 'rgba(255,255,255,0.7)')
    .attr('font-family', 'JetBrains Mono, monospace')
    .text(d => d.sha.slice(0,5));

  // Commit message (two lines, below node)
  nodeGs.each(function(d) {
    const g = d3.select(this);
    const lines = d.label.split('\n');
    lines.forEach((line, i) => {
      g.append('text')
        .attr('text-anchor', 'middle')
        .attr('y', NODE_R + 10 + i * 11)
        .attr('font-size', 7.5)
        .attr('fill', 'rgba(255,255,255,0.38)')
        .attr('font-family', 'JetBrains Mono, monospace')
        .text(line);
    });
  });

  function select(idx) {
    const commit = COMMITS[idx];
    svg.selectAll('.node-ring').attr('opacity', 0);
    svg.selectAll('.node-glow').attr('opacity', 0);
    svg.selectAll('.dag-node')
      .filter(d => d.id === commit.id)
      .select('.node-ring').attr('opacity', 1);
    svg.selectAll('.dag-node')
      .filter(d => d.id === commit.id)
      .select('.node-glow').attr('opacity', 1);

    document.getElementById('dag-branch-label').textContent = commit.branch;
  }

  return { select };
})();

// ═══════════════════════════════════════════════════════════════
// 21-DIMENSION PANEL
// ═══════════════════════════════════════════════════════════════
const DimPanel = (() => {
  const container = document.getElementById('dim-list');

  // Group by category
  const groups = ['core','expr','cc','fx','meta'];
  const groupLabel = {core:'Core',expr:'Expression',cc:'Controllers (CC)',fx:'Effects',meta:'Meta / Structure'};

  groups.forEach(grp => {
    const dims = DIMS_21.filter(d => d.group === grp);
    if (!dims.length) return;

    const label = document.createElement('div');
    label.className = 'dim-group-label';
    label.textContent = groupLabel[grp];
    container.appendChild(label);

    dims.forEach(dim => {
      const row = document.createElement('div');
      row.className = 'dim-row';
      row.id = `dim-row-${dim.id}`;
      row.title = dim.desc;
      row.innerHTML = `
        <div class="dim-dot" id="dim-dot-${dim.id}" style="background:var(--dim)"></div>
        <div class="dim-name">${dim.label}</div>
        <div class="dim-bar-wrap"><div class="dim-bar" id="dim-bar-${dim.id}"></div></div>
      `;
      container.appendChild(row);
    });
  });

  function update(commit) {
    const act = commit.dimAct || {};
    let activeCount = 0;

    DIMS_21.forEach(dim => {
      const level = act[dim.id] || 0;
      const row  = document.getElementById(`dim-row-${dim.id}`);
      const dot  = document.getElementById(`dim-dot-${dim.id}`);
      const bar  = document.getElementById(`dim-bar-${dim.id}`);
      if (!row) return;

      if (level > 0) {
        activeCount++;
        row.classList.add('active');
        dot.style.background = dim.color;
        dot.style.color = dim.color;
        dot.style.boxShadow = `0 0 6px ${dim.color}`;
        bar.style.background = dim.color;
        bar.style.width = `${Math.min(level * 25, 100)}%`;
        bar.style.boxShadow = `0 0 4px ${dim.color}`;
      } else {
        row.classList.remove('active');
        dot.style.background = '#2a3040';
        dot.style.boxShadow = 'none';
        bar.style.background = 'rgba(255,255,255,0.08)';
        bar.style.width = '4%';
        bar.style.boxShadow = 'none';
      }
    });

    document.getElementById('dim-active-count').textContent = `${activeCount} active`;
  }

  return { update };
})();

// ═══════════════════════════════════════════════════════════════
// COMMAND LOG
// ═══════════════════════════════════════════════════════════════
const CmdLog = (() => {
  const log = document.getElementById('cmd-log');

  function show(commit) {
    log.innerHTML = '';

    // Command line
    const cmdLine = document.createElement('div');
    cmdLine.className = 'log-line';
    cmdLine.innerHTML = `<span class="log-prompt">$ </span><span class="log-cmd"></span>`;
    log.appendChild(cmdLine);

    // Output lines
    const outEl = document.createElement('div');
    outEl.className = 'log-line log-out';
    log.appendChild(outEl);

    // Stats line
    const statsLine = document.createElement('div');
    statsLine.className = 'log-line';
    statsLine.innerHTML = `<span style="color:var(--muted);font-size:11px">[${commit.stats}]</span>`;
    log.appendChild(statsLine);

    // Cursor
    const cursor = document.createElement('div');
    cursor.className = 'log-line';
    cursor.innerHTML = '<span class="log-cursor"></span>';
    log.appendChild(cursor);

    // Typewriter effect for command
    const cmdSpan = cmdLine.querySelector('.log-cmd');
    let i = 0;
    const cmdText = commit.command;
    const timer = setInterval(() => {
      cmdSpan.textContent = cmdText.slice(0, ++i);
      if (i >= cmdText.length) {
        clearInterval(timer);
        // Show output after command types
        setTimeout(() => {
          outEl.textContent = commit.output;
          log.scrollTop = log.scrollHeight;
        }, 100);
      }
    }, 18);
    log.scrollTop = 0;
  }

  return { show };
})();

// ═══════════════════════════════════════════════════════════════
// HEATMAP
// ═══════════════════════════════════════════════════════════════
(function buildHeatmap() {
  const container = document.getElementById('heatmap-svg');
  const CELL_W = 56, CELL_H = 18, LABEL_W = 120, TOP_H = 55;
  const SVG_W = LABEL_W + COMMITS.length * CELL_W + 20;
  const SVG_H = TOP_H + DIMS_21.length * CELL_H + 10;

  const svg = d3.select('#heatmap-svg')
    .attr('width', SVG_W).attr('height', SVG_H);

  // Column headers (commit shas)
  COMMITS.forEach((c, ci) => {
    const x = LABEL_W + ci * CELL_W + CELL_W / 2;
    const col = c.branch === 'main' ? '#00ff87' :
                c.branch.includes('lower') ? '#7c6cff' : '#00d4ff';
    svg.append('text')
      .attr('x', x).attr('y', TOP_H - 28)
      .attr('text-anchor', 'middle')
      .attr('font-size', 8).attr('fill', col)
      .attr('font-family', 'JetBrains Mono, monospace')
      .text(c.sha.slice(0,5));

    // Branch dot
    svg.append('circle')
      .attr('cx', x).attr('cy', TOP_H - 16)
      .attr('r', 4)
      .attr('fill', col)
      .attr('opacity', 0.6);

    // Vertical branch line
    svg.append('line')
      .attr('x1', x).attr('x2', x)
      .attr('y1', TOP_H - 10).attr('y2', TOP_H)
      .attr('stroke', col).attr('stroke-width', 1)
      .attr('stroke-opacity', 0.3);
  });

  // Row labels + cells
  DIMS_21.forEach((dim, di) => {
    const y = TOP_H + di * CELL_H;

    // Row background (alternating)
    svg.append('rect')
      .attr('x', 0).attr('y', y)
      .attr('width', SVG_W).attr('height', CELL_H)
      .attr('fill', di % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent');

    // Dimension label
    svg.append('text')
      .attr('x', LABEL_W - 6).attr('y', y + CELL_H * 0.67)
      .attr('text-anchor', 'end')
      .attr('font-size', 9).attr('fill', 'rgba(255,255,255,0.4)')
      .attr('font-family', 'JetBrains Mono, monospace')
      .text(dim.label);

    // Cells
    COMMITS.forEach((c, ci) => {
      const level = (c.dimAct && c.dimAct[dim.id]) || 0;
      const x = LABEL_W + ci * CELL_W;
      const alpha = level === 0 ? 0.04 : level === 1 ? 0.25 : level === 2 ? 0.5 : level === 3 ? 0.72 : 0.92;

      const cell = svg.append('rect')
        .attr('x', x + 1).attr('y', y + 1)
        .attr('width', CELL_W - 2).attr('height', CELL_H - 2)
        .attr('rx', 2)
        .attr('fill', level > 0 ? dim.color : 'rgba(255,255,255,0.06)')
        .attr('opacity', alpha)
        .attr('cursor', 'pointer');

      cell.on('mouseenter', function() {
        if (level > 0) {
          d3.select(this).attr('opacity', Math.min(alpha + 0.2, 1));
          d3.select(this).attr('stroke', dim.color).attr('stroke-width', 1);
        }
      }).on('mouseleave', function() {
        d3.select(this).attr('opacity', alpha).attr('stroke', 'none');
      });

      // Level indicator
      if (level > 0) {
        svg.append('text')
          .attr('x', x + CELL_W / 2).attr('y', y + CELL_H * 0.68)
          .attr('text-anchor', 'middle')
          .attr('font-size', 7.5)
          .attr('fill', 'rgba(255,255,255,0.7)')
          .attr('font-family', 'JetBrains Mono, monospace')
          .text(level === 1 ? '·' : level === 2 ? '●' : level === 3 ? '★' : '★★');
      }
    });
  });

  // Heatmap selected commit highlight column
  window._heatmapHighlight = function(idx) {
    svg.selectAll('.hm-col-hl').remove();
    const x = LABEL_W + idx * CELL_W;
    svg.insert('rect', ':first-child')
      .attr('class', 'hm-col-hl')
      .attr('x', x).attr('y', TOP_H - 2)
      .attr('width', CELL_W).attr('height', SVG_H - TOP_H + 2)
      .attr('fill', 'rgba(255,255,255,0.04)')
      .attr('stroke', 'rgba(255,255,255,0.1)')
      .attr('stroke-width', 1)
      .attr('rx', 2);
  };
})();

// ═══════════════════════════════════════════════════════════════
// AUDIO ENGINE (Tone.js + Salamander Grand Piano)
// ═══════════════════════════════════════════════════════════════
let piano = null;
let reverb = null;
let scheduledNoteIds = [];

async function initAudio() {
  if (state.audioLoading || state.audioReady) return;
  state.audioLoading = true;

  const dot = document.getElementById('audio-dot');
  const lbl = document.getElementById('audio-label');
  dot.className = 'audio-dot loading';
  lbl.textContent = 'Loading piano…';

  await Tone.start();

  reverb = new Tone.Reverb({ decay: 2.5, wet: 0.28 }).toDestination();

  piano = new Tone.Sampler({
    urls: {
      A0: 'A0.mp3',  C1: 'C1.mp3',  'D#1': 'Ds1.mp3', 'F#1': 'Fs1.mp3',
      A1: 'A1.mp3',  C2: 'C2.mp3',  'D#2': 'Ds2.mp3', 'F#2': 'Fs2.mp3',
      A2: 'A2.mp3',  C3: 'C3.mp3',  'D#3': 'Ds3.mp3', 'F#3': 'Fs3.mp3',
      A3: 'A3.mp3',  C4: 'C4.mp3',  'D#4': 'Ds4.mp3', 'F#4': 'Fs4.mp3',
      A4: 'A4.mp3',  C5: 'C5.mp3',  'D#5': 'Ds5.mp3', 'F#5': 'Fs5.mp3',
      A5: 'A5.mp3',  C6: 'C6.mp3',  'D#6': 'Ds6.mp3', 'F#6': 'Fs6.mp3',
      A6: 'A6.mp3',  C7: 'C7.mp3',  'D#7': 'Ds7.mp3', 'F#7': 'Fs7.mp3',
      A7: 'A7.mp3',  C8: 'C8.mp3',
    },
    release: 1.2,
    baseUrl: 'https://tonejs.github.io/audio/salamander/',
    onload: () => {
      state.audioReady = true;
      state.audioLoading = false;
      dot.className = 'audio-dot ready';
      lbl.textContent = 'Piano ready';
      document.getElementById('btn-init-audio').textContent = '🎹 Piano Ready';
      document.getElementById('btn-init-audio').classList.add('active');
      document.getElementById('btn-play').disabled = false;
    },
  }).connect(reverb);
}

function stopPlayback() {
  if (state.rafId) { cancelAnimationFrame(state.rafId); state.rafId = null; }
  state.isPlaying = false;
  state.playheadSec = 0;
  PR.setPlayhead(0);
  updatePlayBtn();
  document.getElementById('progress-fill').style.width = '0%';
  document.getElementById('time-display').textContent = '0:00';
  // Tone.js: cancel all pending events
  try { Tone.getTransport().stop(); Tone.getTransport().cancel(); } catch(e) {}
}

function playNotes(notes) {
  if (!piano || !state.audioReady) return;
  stopPlayback();

  const minStart = notes.length ? Math.min(...notes.map(n => n[2])) : 0;
  const now = Tone.now() + 0.3;
  state.playStartWallClock = performance.now();
  state.playStartAudioSec  = minStart;
  state.isPlaying = true;
  updatePlayBtn();

  // Schedule all notes via Tone.js (offloads to Web Audio scheduler)
  notes.forEach(n => {
    const t = now + (n[2] - minStart);
    const noteName = Tone.Frequency(n[0], 'midi').toNote();
    const dur = Math.max(n[3], 0.06);
    const vel = n[1] / 127;
    try { piano.triggerAttackRelease(noteName, dur, t, vel); } catch(e) {}
  });

  // Animate playhead
  const totalDur = notes.length ? Math.max(...notes.map(n => n[2] + n[3])) - minStart : 0;

  function tick() {
    if (!state.isPlaying) return;
    const elapsed = (performance.now() - state.playStartWallClock) / 1000;
    const sec = state.playStartAudioSec + elapsed;
    PR.setPlayhead(sec);
    document.getElementById('time-display').textContent = fmtTime(elapsed);
    document.getElementById('progress-fill').style.width =
      totalDur > 0 ? `${Math.min(elapsed / totalDur * 100, 100)}%` : '0%';
    if (elapsed < totalDur + 0.5) {
      state.rafId = requestAnimationFrame(tick);
    } else {
      stopPlayback();
    }
  }
  state.rafId = requestAnimationFrame(tick);
}

// ═══════════════════════════════════════════════════════════════
// COMMIT SELECTION (main controller)
// ═══════════════════════════════════════════════════════════════
function selectCommit(idx) {
  state.commitIdx = Math.max(0, Math.min(idx, COMMITS.length - 1));
  const commit = getCommit(state.commitIdx);

  // Update all panels
  PR.update(commit);
  DAG.select(state.commitIdx);
  DimPanel.update(commit);
  CmdLog.show(commit);
  if (window._heatmapHighlight) window._heatmapHighlight(state.commitIdx);

  // Update control info
  document.getElementById('commit-sha-disp').textContent = commit.sha;
  document.getElementById('commit-msg-disp').textContent = commit.message;

  // Play if audio ready
  if (state.audioReady && commit.filter) {
    const notes = getNotesForCommit(commit);
    playNotes(notes);
  }

  updateControls();
}

function updateControls() {
  document.getElementById('btn-first').disabled = state.commitIdx === 0;
  document.getElementById('btn-prev').disabled  = state.commitIdx === 0;
  document.getElementById('btn-next').disabled  = state.commitIdx === COMMITS.length - 1;
  document.getElementById('btn-last').disabled  = state.commitIdx === COMMITS.length - 1;
}

function updatePlayBtn() {
  const btn = document.getElementById('btn-play');
  btn.textContent = state.isPlaying ? '⏸' : '▶';
  btn.className   = 'ctrl-play' + (state.isPlaying ? ' playing' : '');
}

// ═══════════════════════════════════════════════════════════════
// CLI REFERENCE DATA
// ═══════════════════════════════════════════════════════════════
const CLI_CMDS = [
  {
    name: 'muse notes',
    desc: 'Display notes as a musical notation table (bar / beat / pitch / velocity / duration).',
    flags: [
      { name: '--bar <range>', desc: 'Filter to specific bars, e.g. 1-8 or 3' },
      { name: '--track <path>', desc: 'Restrict to a specific MIDI track file' },
      { name: '--voice <n>', desc: 'Filter to a MIDI channel/voice (1-16)' },
      { name: '--format', desc: 'Output format: table (default), csv, json' },
    ],
    returns: 'Table rows: bar, beat, pitch (name), MIDI#, velocity, dur(ticks)',
  },
  {
    name: 'muse piano-roll',
    desc: 'Render an ASCII piano roll of committed MIDI notes.',
    flags: [
      { name: '--bars <range>', desc: 'Bars to display (default: all)' },
      { name: '--resolution', desc: 'Ticks per cell: 12 (16th), 6 (32nd), 3 (64th)' },
      { name: '--color', desc: 'Enable ANSI color output (voice-coded)' },
      { name: '--width <n>', desc: 'Terminal width in chars (default: 120)' },
    ],
    returns: 'ASCII roll: pitch axis (Y), time axis (X), ● for note-on',
  },
  {
    name: 'muse harmony',
    desc: 'Chord analysis, key detection (Krumhansl-Schmuckler), and pitch-class histogram.',
    flags: [
      { name: '--bar <range>', desc: 'Analyse a specific bar range' },
      { name: '--window <n>', desc: 'Sliding window in bars (default: 4)' },
      { name: '--output', desc: 'table | json | histogram' },
    ],
    returns: 'Key guess, chord per bar, pitch-class distribution',
  },
  {
    name: 'muse velocity-profile',
    desc: 'Dynamic range analysis — velocity histogram and per-bar statistics.',
    flags: [
      { name: '--bar <range>', desc: 'Restrict to a bar range' },
      { name: '--bins <n>', desc: 'Histogram bucket count (default: 16)' },
    ],
    returns: 'Min/max/mean velocity, per-bar average, ASCII histogram',
  },
  {
    name: 'muse note-log',
    desc: 'Note-level change history — equivalent of git log -p but for MIDI notes.',
    flags: [
      { name: '--bar <range>', desc: 'Filter by bar number' },
      { name: '--pitch <p>', desc: 'Filter by MIDI pitch (e.g. 60, C4)' },
      { name: '--oneline', desc: 'Compact one-line-per-commit format' },
      { name: '--since <ref>', desc: 'Start from a commit ref' },
    ],
    returns: '+note / -note rows per commit, with pitch, velocity, timing',
  },
  {
    name: 'muse note-blame',
    desc: 'Per-bar attribution — which commit last touched each bar.',
    flags: [
      { name: '--bar <range>', desc: 'Annotate specific bars only' },
      { name: '--track <path>', desc: 'Target MIDI file' },
      { name: '--porcelain', desc: 'Machine-readable output' },
    ],
    returns: 'bar#  commit-sha  author  message (one row per bar)',
  },
  {
    name: 'muse note-hotspots',
    desc: 'Bar-level churn leaderboard — which bars changed most frequently.',
    flags: [
      { name: '--top <n>', desc: 'Show top N bars (default: 10)' },
      { name: '--since <ref>', desc: 'Limit history range' },
    ],
    returns: 'Ranked list: bar, change count, last modified commit',
  },
  {
    name: 'muse transpose',
    desc: 'Surgically shift pitches by semitone interval without creating a new commit.',
    flags: [
      { name: '--semitones <n>', desc: 'Number of semitones to shift (+/-)' },
      { name: '--bar <range>', desc: 'Restrict transposition to a bar range' },
      { name: '--voice <n>', desc: 'Restrict to a specific MIDI voice/channel' },
      { name: '--dry-run', desc: 'Preview without writing changes' },
      { name: '--clamp', desc: 'Clamp to valid MIDI range 0–127 instead of error' },
    ],
    returns: 'Modified MIDI file written to muse-work/; use muse diff to review',
  },
  {
    name: 'muse mix',
    desc: 'Layer two MIDI tracks into a single output track with channel remapping.',
    flags: [
      { name: '--channel-a <n>', desc: 'Output channel for first source' },
      { name: '--channel-b <n>', desc: 'Output channel for second source' },
      { name: '--out <path>', desc: 'Destination file path' },
    ],
    returns: 'Merged MIDI file written to muse-work/; commit to persist',
  },
  {
    name: 'muse midi-query',
    desc: 'Structured query against MIDI content using the Muse query DSL.',
    flags: [
      { name: '--where', desc: 'Filter expression, e.g. "pitch > 60 AND velocity > 90"' },
      { name: '--select', desc: 'Output columns: pitch, velocity, bar, beat, dur' },
      { name: '--limit <n>', desc: 'Max rows returned' },
      { name: '--format', desc: 'table | json | csv' },
    ],
    returns: 'Filtered note table matching the query predicate',
  },
  {
    name: 'muse midi-check',
    desc: 'Validate MIDI invariants: no stuck notes, tempo consistency, no out-of-range events.',
    flags: [
      { name: '--strict', desc: 'Error on warnings (not just errors)' },
      { name: '--fix', desc: 'Auto-correct recoverable violations' },
    ],
    returns: 'Pass/fail report with violation details and line references',
  },
  {
    name: 'muse diff',
    desc: 'Show structured delta between two commits or working tree vs HEAD.',
    flags: [
      { name: '<ref1>', desc: 'Base commit SHA or branch name' },
      { name: '<ref2>', desc: 'Target commit SHA or branch name (default: HEAD)' },
      { name: '--dimension <d>', desc: 'Filter to a specific MIDI dimension' },
      { name: '--stat', desc: 'Summary statistics only (note counts per dimension)' },
    ],
    returns: 'InsertOp / DeleteOp / MutateOp per note with full field details',
  },
];

// ═══════════════════════════════════════════════════════════════
// BUILD CLI REFERENCE
// ═══════════════════════════════════════════════════════════════
(function buildCLI() {
  const grid = document.getElementById('cli-grid');
  CLI_CMDS.forEach(cmd => {
    const card = document.createElement('div');
    card.className = 'cmd-card';
    card.innerHTML = `
      <div class="cmd-name">${cmd.name}</div>
      <div class="cmd-desc">${cmd.desc}</div>
      <div class="cmd-flags">
        ${cmd.flags.map(f => `
          <div class="cmd-flag">
            <span class="flag-name">${f.name}</span>
            <span class="flag-desc">${f.desc}</span>
          </div>
        `).join('')}
      </div>
      <div class="cmd-return">Returns: <span>${cmd.returns}</span></div>
    `;
    grid.appendChild(card);
  });
})();

// ═══════════════════════════════════════════════════════════════
// WIRE UP CONTROLS
// ═══════════════════════════════════════════════════════════════
document.getElementById('btn-init-audio').addEventListener('click', initAudio);

document.getElementById('btn-play').addEventListener('click', () => {
  if (!state.audioReady) { initAudio(); return; }
  if (state.isPlaying) {
    stopPlayback();
  } else {
    const notes = getNotesForCommit(getCommit(state.commitIdx));
    playNotes(notes);
  }
});

document.getElementById('btn-first').addEventListener('click', () => selectCommit(0));
document.getElementById('btn-last').addEventListener('click', () => selectCommit(COMMITS.length - 1));
document.getElementById('btn-prev').addEventListener('click', () => selectCommit(state.commitIdx - 1));
document.getElementById('btn-next').addEventListener('click', () => selectCommit(state.commitIdx + 1));

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') selectCommit(state.commitIdx + 1);
  if (e.key === 'ArrowLeft')  selectCommit(state.commitIdx - 1);
  if (e.key === ' ') { e.preventDefault(); document.getElementById('btn-play').click(); }
});

// ═══════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════
document.getElementById('btn-play').disabled = true;
document.getElementById('time-total').textContent = fmtTime(TOTAL_DURATION);

// Start with commit 0 (muse init, no notes)
selectCommit(0);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = pathlib.Path("artifacts/midi-demo.html")
    out.parent.mkdir(exist_ok=True)
    content = render_midi_demo()
    out.write_text(content, encoding="utf-8")
    kb = len(content) // 1024
    logger.info("✅  artifacts/midi-demo.html written (%d KB)", kb)
