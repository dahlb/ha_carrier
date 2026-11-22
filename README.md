# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                    |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| custom\_components/ha\_carrier/\_\_init\_\_.py          |      154 |        2 |       36 |        2 |     98% |  221, 417 |
| custom\_components/ha\_carrier/binary\_sensor.py        |       84 |        0 |        8 |        0 |    100% |           |
| custom\_components/ha\_carrier/climate.py               |      290 |        0 |      104 |        0 |    100% |           |
| custom\_components/ha\_carrier/config\_flow.py          |      119 |        1 |       36 |        2 |     98% |223, 275-\>304 |
| custom\_components/ha\_carrier/const.py                 |       41 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/coordinator.py           |      289 |        1 |       94 |        0 |     99% |       739 |
| custom\_components/ha\_carrier/diagnostics.py           |       40 |        0 |        8 |        1 |     98% | 121-\>104 |
| custom\_components/ha\_carrier/entity.py                |      114 |        1 |       38 |        1 |     99% |        66 |
| custom\_components/ha\_carrier/entry\_level\_climate.py |      126 |        0 |       36 |        0 |    100% |           |
| custom\_components/ha\_carrier/exceptions.py            |        2 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/migrate.py               |      221 |        1 |      108 |        8 |     97% |221, 270-\>279, 344-\>351, 415-\>418, 420-\>422, 422-\>425, 437-\>452, 463-\>452 |
| custom\_components/ha\_carrier/resiliency.py            |      102 |        0 |       30 |        0 |    100% |           |
| custom\_components/ha\_carrier/select.py                |       49 |        0 |        6 |        0 |    100% |           |
| custom\_components/ha\_carrier/sensor.py                |      369 |        0 |       60 |        0 |    100% |           |
| custom\_components/ha\_carrier/services.py              |       22 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/util.py                  |       73 |        0 |       34 |        0 |    100% |           |
| **TOTAL**                                               | **2095** |    **6** |  **598** |   **14** | **99%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dahlb/ha_carrier/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fdahlb%2Fha_carrier%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.