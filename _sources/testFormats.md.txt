# Supported Data Formats

The deterministic test generator reads output files produced by pipeline
steps and generates integrity, qualitative, and quantitative tests for
each file. Format detection is automatic based on file extension.

## Format Table

| Format | Extensions | Library Required | Domain |
|--------|-----------|-----------------|--------|
| NumPy array | `.npy` | numpy | General |
| NumPy archive | `.npz` | numpy | General |
| JSON | `.json` | (stdlib) | General |
| JSON Lines | `.jsonl`, `.ndjson` | (stdlib) | General |
| CSV | `.csv` | (stdlib) | General |
| HDF5 | `.h5`, `.hdf5` | h5py | General |
| Whitespace-delimited | `.dat`, `.txt` | (stdlib) | General |
| Key-value text | (via `sFormat` override) | (stdlib) | General |
| Fixed-width text | (via `sFormat` override) | (stdlib) | General |
| Multi-table text | (via `sFormat` override) | (stdlib) | General |
| Excel | `.xlsx`, `.xls` | openpyxl | General |
| Parquet | `.parquet` | pyarrow | Data Science |
| Image | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif` | Pillow | General |
| FITS | `.fits`, `.fit` | astropy | Astronomy |
| VOTable | `.vot` | astropy | Astronomy |
| IPAC table | `.ipac` | astropy | Astronomy |
| MATLAB | `.mat` | scipy | Engineering |
| FORTRAN binary | `.unf` | scipy | Engineering |
| VTK mesh | `.vtk`, `.vtu` | pyvista | Engineering |
| CGNS | `.cgns` | h5py | Engineering |
| FASTA | `.fasta`, `.fa` | (stdlib) | Biology |
| FASTQ | `.fastq`, `.fq` | (stdlib) | Biology |
| VCF | `.vcf` | (stdlib) | Biology |
| BED | `.bed` | (stdlib) | Biology |
| GFF/GTF | `.gff`, `.gtf`, `.gff3` | (stdlib) | Biology |
| SAM | `.sam` | (stdlib) | Biology |
| BAM | `.bam` | pysam | Biology |
| SPSS | `.sav` | pyreadstat | Social Science |
| Stata | `.dta` | pyreadstat | Social Science |
| SAS | `.sas7bdat` | pyreadstat | Social Science |
| R data | `.rds`, `.RData`, `.rda` | pyreadr | Social Science |
| Safetensors | `.safetensors` | safetensors | AI/ML |
| TFRecord | `.tfrecord` | tfrecord | AI/ML |
| Syslog | `.log` | (stdlib) | Security |
| CEF | `.cef` | (stdlib) | Security |
| PCAP | `.pcap`, `.pcapng` | scapy | Security |

**Total: 36 format names, 50 file extensions.**

## How Format Detection Works

1. The file extension is matched against the format map above.
2. For `.txt` and `.dat` files, the system checks whether a majority of
   non-blank, non-comment lines contain `=` signs. If so, the file is
   treated as key-value format rather than whitespace-delimited.
3. For unknown extensions, the first 4 bytes are read. If any byte exceeds
   ASCII range (value > 127), the file is reported as an unsupported binary
   format and skipped. Otherwise it is treated as whitespace-delimited text.

## Optional Libraries

Formats marked `(stdlib)` require no additional packages. All other
libraries are imported with `try/except ImportError`, so a missing library
does not break the test generator -- the file is reported with an error
message indicating which package to install, and the remaining files are
processed normally.

## Overriding Format Detection

When a file extension is ambiguous (for example, a `.txt` file that uses
fixed-width columns rather than whitespace delimiters), set the `sFormat`
field in the quantitative standards JSON to override extension-based
detection:

```json
{
    "sName": "fTemperature",
    "sDataFile": "results.txt",
    "sAccessPath": "column:TGlobal,index:-1",
    "sFormat": "fixedwidth",
    "fValue": 288.15
}
```

Valid `sFormat` values are the format names in the table above.

## Access Path Syntax

Each quantitative benchmark specifies an access path that tells the test
runner how to locate a value within a file. The syntax depends on the
format:

| Format Family | Access Path Example | Meaning |
|--------------|-------------------|---------|
| CSV, whitespace, Excel, SPSS, Stata, SAS, VOTable, IPAC | `column:Temperature,index:-1` | Last row of Temperature column |
| CSV, whitespace | `column:Temperature,index:mean` | Mean of Temperature column |
| NumPy, MATLAB, safetensors | `key:arrayName,index:0` | First element of named array |
| NumPy, MATLAB, safetensors | `key:arrayName,index:mean` | Mean of named array |
| NumPy (.npy) | `index:0` | First element (flat) |
| JSON | `key:path.to.field` | Nested key traversal |
| JSON | `key:daMedians,index:0` | First element of JSON array |
| JSON | `key:daMedians,index:mean` | Mean of JSON array |
| HDF5, CGNS | `dataset:/group/name,index:0` | First element of dataset |
| FITS | `hdu:1,column:flux,index:0` | First row of flux column in HDU 1 |
| FITS | `hdu:0,index:mean` | Mean of image data in HDU 0 |
| FASTA, FASTQ | `index:mean` | Mean sequence length |
| VCF, BED, GFF, SAM | `column:POS,index:0` | First value in POS column |
| Key-value | `key:parameterName` | Value associated with key |
| PCAP | `index:mean` | Mean packet length |
| Syslog, CEF | `index:0` | Line count |
| Multi-table | `section:0,column:X,index:0` | First value in column X of first table |

## Security

- All `np.load()` calls use `allow_pickle=False` to prevent arbitrary code
  execution via malicious NumPy files.
- PyTorch checkpoint files (`.pt`, `.pth`) are intentionally unsupported
  because they use pickle deserialization internally. Use safetensors instead.
- File paths are validated with `os.path.realpath()` to prevent path
  traversal attacks.
- Files larger than 500 MB are skipped to prevent memory exhaustion.
- JSON traversal is limited to 10 levels of nesting depth.
- Each file generates at most 250 benchmark entries to prevent test
  explosion on wide datasets.

## Unsupported Files

Files with unrecognized extensions are handled as follows:

1. The first 4 bytes are inspected. If any byte is non-ASCII (> 127), the
   file is classified as an unsupported binary format.
2. For binary files, the introspection reports `bLoadable: false` with
   `sError: "unsupported binary format"`. No benchmarks are generated, but
   the integrity test still verifies the file exists and is non-empty.
3. For text files with unknown extensions, the system falls back to
   whitespace-delimited parsing with automatic header detection.

To add support for a new format, add an entry to `_DICT_FORMAT_MAP`, a
loader function to the template, a benchmarker to the introspection script,
and integrity/no-NaN test generators. All new library imports must use
`try/except ImportError` for graceful degradation.
