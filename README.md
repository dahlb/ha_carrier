# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/dahlb/ha_carrier/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                 |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| custom\_components/ha\_carrier/\_\_init\_\_.py                       |      108 |       39 |       16 |        6 |     62% |61-64, 122-124, 128-143, 162-182, 217, 220-223, 227, 229, 234, 250-\>255 |
| custom\_components/ha\_carrier/binary\_sensor.py                     |       54 |        2 |        6 |        2 |     93% |37-\>43, 77, 93 |
| custom\_components/ha\_carrier/carrier\_data\_update\_coordinator.py |      161 |       20 |       42 |        6 |     85% |157, 230-231, 272, 319, 342-\>348, 352-359, 393-394, 400, 403-411, 445, 460-461 |
| custom\_components/ha\_carrier/carrier\_entity.py                    |       80 |        9 |       24 |        9 |     83% |70-77, 112, 127-\>126, 129-130, 144-\>143, 146-147, 187, 213-\>215 |
| custom\_components/ha\_carrier/climate.py                            |      227 |       25 |       86 |       19 |     83% |65-\>68, 68-\>70, 70-\>72, 72-\>75, 157, 164, 189, 199, 204, 221, 295-\>exit, 306-307, 337-346, 371, 410-\>413, 457, 462-463, 465-466, 469 |
| custom\_components/ha\_carrier/config\_flow.py                       |      127 |       12 |       38 |       12 |     85% |60-\>67, 63-64, 68-69, 154-\>171, 161, 197-198, 213, 221, 265-\>294, 267-268, 280-\>285, 286 |
| custom\_components/ha\_carrier/const.py                              |       31 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/diagnostics.py                        |       32 |        0 |        8 |        2 |     95% |63-\>46, 80-\>87 |
| custom\_components/ha\_carrier/exceptions.py                         |        2 |        0 |        0 |        0 |    100% |           |
| custom\_components/ha\_carrier/migrate.py                            |      228 |       43 |      108 |       25 |     76% |98, 114, 127-131, 179, 183-185, 189, 223, 225-233, 244-249, 253, 272-281, 286-295, 300, 410-\>413, 413-\>415, 415-\>417, 417-\>420, 433-\>445, 441, 456-\>445, 489, 498-507, 512-521, 593, 668-\>682, 671-679 |
| custom\_components/ha\_carrier/resiliency.py                         |      104 |        8 |       30 |        6 |     90% |167-173, 260, 266, 273, 279, 283, 289 |
| custom\_components/ha\_carrier/select.py                             |       52 |        6 |        8 |        4 |     83% |66, 82, 111, 127, 158-159 |
| custom\_components/ha\_carrier/sensor.py                             |      353 |       60 |      106 |       41 |     78% |76-\>82, 82-\>88, 88-\>94, 116-\>130, 170, 187, 287-289, 294-295, 299-301, 305-\>309, 306-\>305, 310-316, 325-332, 362-363, 367-369, 373-\>377, 374-\>373, 378-384, 419-420, 424-426, 430-\>429, 432-\>429, 436, 467-468, 472-474, 480-\>477, 484, 515-516, 520-522, 528-\>525, 532, 564, 620-621, 649-650, 678-679, 737-738, 792, 801, 803-\>exit, 831-832, 837, 869-870 |
| custom\_components/ha\_carrier/util.py                               |       85 |        6 |       38 |        7 |     89% |117, 121, 169, 172, 180, 205, 228-\>223 |
| **TOTAL**                                                            | **1644** |  **230** |  **510** |  **139** | **82%** |           |


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